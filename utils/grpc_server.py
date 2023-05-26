import anyio
import grpc
import utils.config as config
import grpc_utils.nginx_controller_server_pb2 as pb2
import grpc_utils.nginx_controller_server_pb2_grpc as pb2_grpc


class AgentNotifyService(pb2_grpc.AgentNotifyServicer):

    def __init__(self, send_stream):
        self.send_stream = send_stream

    async def notify(self, request, context):
        # Write to the shared queue
        await self.send_stream.send(request.message)

        return pb2.MessageResponse(received=True)


class GRPCServer:
    def __init__(self, send_stream):
        self.__server = None
        self.is_running = False
        self.send_stream = send_stream
        self.server_started_event = anyio.Event()

    async def start(self):
        self.__server = grpc.aio.server()
        pb2_grpc.add_AgentNotifyServicer_to_server(AgentNotifyService(self.send_stream), self.__server)
        self.__server.add_insecure_port(f'[::]:{config.GRPC_PORT}')
        await self.__server.start()
        self.is_running = True
        await self.server_started_event.set()
        await self.__server.wait_for_termination()

    async def stop(self):
        # Stop the server
        if self.__server is not None:
            await self.__server.stop(False)
            self.is_running = False
            self.__server = None
