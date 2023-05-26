import asyncclick as click
from utils.nginx_controller import NginxController, AbortOperationException

nginx_controller = NginxController()


@click.group()
async def cli():
    pass


@cli.command()
@click.argument("file_path")
@click.argument("version")
@click.option("--publish", is_flag=True, help="Boolean. Flag for publishing the version to running nginx server")
async def create_nginx_conf_version(file_path, version, publish=False):
    """
    Creates a new version of nginx configuration file and publishes the config if instructed (see options).

    :param file_path: String. The file path to the new nginx configuration file
    :param version: String. Version of the nginx config
    """
    try:
        nginx_conf = await nginx_controller.create_config_version(file_path, version)

        if publish:
            await nginx_controller.publish_config(version, nginx_conf=nginx_conf)
    except AbortOperationException as abort_ex:
        print(abort_ex)
    except Exception as ex:
        print(f"An error has occurred: {ex}")


@cli.command()
@click.argument("version")
async def publish_nginx_conf(version):
    try:
        await nginx_controller.publish_config(version)
    except AbortOperationException as abort_ex:
        print(abort_ex)
    except Exception as ex:
        print(f"An error has occurred: {ex}")

#
# @cli.command()
# async def list_nginx_conf_versions():
#     await nginx_controller.get_available_config_versions()


#
# @cli.command()
# async def configure():
#     Ideally we would provide a way to configure global settings like SSH keys to use for hosts, number of Nginx servers per host, etc...

#
# @cli.command()
# async def add_hose():
#     Ideally we would run the agent via ssh in a remote machine


if __name__ == "__main__":
    cli()
