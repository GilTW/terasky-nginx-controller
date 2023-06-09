import json
import anyio
import utils.config as config
import nginxparser_eb as nginx_parser
from datetime import datetime
from utils.grpc_server import GRPCServer
from aws_utils import s3_helper
from alive_progress import alive_bar


class AbortOperationException(Exception):
    def __init__(self, message=""):
        super(AbortOperationException, self).__init__(f"Aborting. {message}")


class NginxController:
    def __init__(self):
        state_file_content = s3_helper.get_file_content(config.DATA_BUCKET, config.STATE_FILE)

        if state_file_content:
            state_json = json.loads(state_file_content)
            self.current_version = state_json["current_version"]
            self.available_versions = set(state_json["available_versions"])
            self.exposed_ports = set(state_json["exposed_ports"])
            self.server_groups = state_json["server_groups"]
        else:
            self.current_version = None
            self.available_versions = set()
            self.exposed_ports = set()
            self.server_groups = {}

    async def add_group(self, group_name, nginx_servers_count):
        self.server_groups[group_name] = {"nginx_servers_count": nginx_servers_count}
        await self.__update_state()

    async def list_available_config_versions(self):
        for _version in self.available_versions:
            print(_version)

    async def create_config_version(self, file_path, version, ask_overwrite):
        is_overwrite = False

        if version in self.available_versions:
            if not ask_overwrite:
                is_overwrite = True
            else:
                print(f"Version '{version}' already exists, do you want to overwrite it? y/n")
                is_overwrite = input("> ").lower() == "y"

            if not is_overwrite:
                raise AbortOperationException("User aborted operation.")

        print(f"Loading configuration file from '{file_path}'...")

        with open(file_path, "r") as config_file:
            nginx_conf = nginx_parser.load(config_file)

        nginx_conf_str = str(nginx_conf)

        if "'http'" in nginx_conf_str:
            for block in nginx_conf:
                if "'http'" in str(block):
                    block[1].append(self.__create_config_version_server_block(version))
                    break
        else:
            nginx_conf.append(self.__create_config_version_server_block(version))

        # noinspection PyTypeChecker
        nginx_conf_modified_str = nginx_parser.dumps(nginx_conf)
        nginx_conf_bucket_key = f"{config.CONFIG_VERSIONS_BUCKET_FOLDER}/{config.CONFIG_FILE_NAME_PATTERN.format(version=version)}"
        s3_helper.save_file_content(config.DATA_BUCKET, nginx_conf_bucket_key, nginx_conf_modified_str)

        if not is_overwrite:
            self.available_versions.add(version)
            await self.__update_state()

        print(f"Config file has been successfully created for version '{version}'!")

        return nginx_conf

    async def publish_config(self, version, nginx_conf=None, group_gradual=False, force_publish=False):
        if version not in self.available_versions:
            raise AbortOperationException(f"Version '{version}' is not available for publishing!")

        if not self.server_groups:
            raise AbortOperationException("There are no Nginx server groups configured!")

        if version == self.current_version and not force_publish:
            raise AbortOperationException(f"Running version is already '{version}'!")

        if not nginx_conf:
            nginx_conf_bucket_key = f"{config.CONFIG_VERSIONS_BUCKET_FOLDER}/{config.CONFIG_FILE_NAME_PATTERN.format(version=version)}"
            nginx_conf_file_content = s3_helper.get_file_content(config.DATA_BUCKET, nginx_conf_bucket_key)

            if nginx_conf_file_content:
                nginx_conf = nginx_parser.loads(nginx_conf_file_content)
            else:
                raise AbortOperationException(f"No Nginx configuration file for version '{version}' has been found!")

        exposed_ports = self.__find_exposed_ports(nginx_conf)
        publishing_instructions = {
            "version": version,
            "exposed_ports": list(exposed_ports),
            "timestamp": datetime.now().timestamp()
        }

        if exposed_ports != self.exposed_ports and self.current_version is not None:
            print("Publishing this version will require a restart, would you like to continue? y/n")

            if input("> ").lower() != "y":
                raise AbortOperationException()
            else:
                publishing_instructions["restart_required"] = True

        await self.__start_publish(publishing_instructions, group_gradual)
        self.current_version = version
        self.exposed_ports = exposed_ports
        await self.__update_state()

    async def __update_state(self):
        state_data = {
            "current_version": self.current_version,
            "available_versions": list(self.available_versions),
            "exposed_ports": list(self.exposed_ports),
            "server_groups": self.server_groups
        }

        s3_helper.save_file_content(config.DATA_BUCKET, config.STATE_FILE, json.dumps(state_data))

    async def __start_publish(self, publishing_instructions, group_gradual):
        version = publishing_instructions['version']
        send_stream, receive_stream = anyio.create_memory_object_stream(1000)
        grpc_server = GRPCServer(send_stream)
        publish_state_controller = NginxController.PublishStateController(self.server_groups, receive_stream)
        print(f"Publishing version '{version}' to {publish_state_controller.total_nginx_servers} Nginx servers across "
              f"{publish_state_controller.total_nginx_server_groups} groups.")

        async with anyio.create_task_group() as aio_task_group:
            try:
                aio_task_group.start_soon(grpc_server.start)
                aio_task_group.start_soon(publish_state_controller.run)
                await grpc_server.server_started_event.wait()
                await publish_state_controller.listen_to_agent_start_event.wait()

                async with anyio.move_on_after(config.PUBLISH_TIMEOUT_SECONDS) as timeout_scope:
                    for _server_group in self.server_groups:
                        aio_task_group.start_soon(publish_state_controller.publish_group, _server_group, publishing_instructions)

                        if group_gradual:
                            await publish_state_controller.publish_state_view[_server_group]["done_event"].wait()

                    await publish_state_controller.publish_done_event.wait()
                    await grpc_server.stop()

                if timeout_scope.cancel_called:
                    raise AbortOperationException("Publish timeout has reached!")

                print(f"Published version '{version}' successfully!")
            finally:
                if grpc_server.is_running:
                    await grpc_server.stop()

                await send_stream.aclose()
                await receive_stream.aclose()

    @staticmethod
    def __find_exposed_ports(nginx_conf):
        exposed_ports = set()

        def recursive_search(nginx_conf_blocks):
            nonlocal exposed_ports

            for block in nginx_conf_blocks:
                block_str = str(block)
                if "'listen'" in block_str and ":" in block_str:
                    if block[0] == "listen":
                        address_and_port_split = block[1].split(":")

                        if address_and_port_split[-1].isdigit() and address_and_port_split[-1] != config.CONFIG_SERVER_PORT:
                            exposed_ports.add(address_and_port_split[-1])
                    else:
                        recursive_search(block)

        recursive_search(nginx_conf)

        if len(exposed_ports) == 0:
            exposed_ports.add("80")

        return exposed_ports

    @staticmethod
    def __create_config_version_server_block(version):
        server_block = [
            ['\n\t    ', 'server', ' '],
            [
                ['\n\t\t    ', 'listen', ' ', config.CONFIG_SERVER_PORT],
                [
                    ['\n\n\t\t\t\t   ', 'location', ' ', '/', ' '],
                    [
                        ['\n\t\t\t\t\t       ', 'return', ' ', f'200 "{version}"']
                    ]
                ]
            ]
        ]

        return server_block

    class PublishStateController:
        def __init__(self, server_groups, receive_stream):
            self.publish_state_view = {}
            self.receive_stream = receive_stream
            self.total_nginx_servers = 0
            self.total_nginx_server_groups = len(server_groups)
            self.listen_to_agent_start_event = anyio.Event()
            self.publish_done_event = anyio.Event()

            for _server_group in server_groups.keys():
                self.publish_state_view[_server_group] = {
                    "servers_count": server_groups[_server_group]["nginx_servers_count"],
                    "status": "PENDING",
                    "servers_done_count": 0,
                    "done_event": anyio.Event()
                }

                self.total_nginx_servers += server_groups[_server_group]["nginx_servers_count"]

        async def run(self):
            await self.listen_to_agent_start_event.set()
            responses_received = 0
            print("")

            with alive_bar(self.total_nginx_servers, title="Total Servers", force_tty=True) as total_servers_bar:
                async for message in self.receive_stream:
                    message_json = json.loads(message)
                    server_group = message_json["server_group"]
                    container_publish_result = message_json["containers_publish_result"]
                    responses_received += 1

                    if container_publish_result == "Success":
                        total_servers_bar()
                        server_group_view = self.publish_state_view[server_group]
                        server_group_view["servers_done_count"] += 1

                        if server_group_view["servers_done_count"] == server_group_view["servers_count"]:
                            print(f"{server_group} Completed Version Publishing!")
                            self.publish_state_view[server_group]["status"] = "COMPLETED"
                            await self.publish_state_view[server_group]["done_event"].set()

                    if responses_received == self.total_nginx_servers:
                        break

            print("")
            await self.publish_done_event.set()

        async def publish_group(self, server_group, publishing_instructions):
            try:
                group_running_version_file_key = f"{config.RUNNING_VERSIONS_BUCKET_FOLDER}/" \
                                                 f"{config.GROUP_RUNNING_VERSION_FILE_NAME_PATTERN.format(group=server_group)}"
                await anyio.to_thread.run_sync(s3_helper.save_file_content, config.DATA_BUCKET, group_running_version_file_key,
                                               json.dumps(publishing_instructions))
                self.publish_state_view[server_group]["status"] = "RUNNING"

            except Exception as ex:
                print(ex)
