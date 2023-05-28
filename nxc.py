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
@click.option("--group-gradual", is_flag=True, help="Boolean. Flag for gradually deploying changes in groups")
async def create_nginx_conf_version(file_path, version, publish=False, group_gradual=False):
    """
    Creates a new version of nginx configuration file and publishes the config if instructed (see options).

    :param file_path: String. The file path to the new nginx configuration file
    :param version: String. Version of the nginx config
    """
    try:
        nginx_conf = await nginx_controller.create_config_version(file_path, version)

        if publish:
            await nginx_controller.publish_config(version, nginx_conf=nginx_conf, group_gradual=group_gradual, force_publish=True)
    except AbortOperationException as abort_ex:
        print(abort_ex)
    except Exception as ex:
        print(f"An error has occurred: {ex}")


@cli.command()
@click.argument("version")
@click.option("--force-publish", is_flag=True, help="Boolean. Flag for forcing version publishing")
@click.option("--group-gradual", is_flag=True, help="Boolean. Flag for gradually deploying changes in groups")
async def publish_nginx_conf(version, force_publish=False, group_gradual=False):
    """
    Publishes a Nginx configuration version
    :param version: String. Version of Nginx configuration to publish
    """
    try:
        await nginx_controller.publish_config(version, group_gradual=group_gradual, force_publish=force_publish)
    except AbortOperationException as abort_ex:
        print(abort_ex)
    except Exception as ex:
        print(f"An error has occurred: {ex}")


@cli.command()
async def list_nginx_conf_versions():
    """
    Lists all available versions for Nginx Controller to publish.
    """
    await nginx_controller.list_available_config_versions()


@cli.command()
@click.argument("group_name")
@click.argument("nginx_servers_count")
async def add_group(group_name, nginx_servers_count):
    await nginx_controller.add_group(group_name, int(nginx_servers_count))


#
# @cli.command()
# async def configure():
#     Ideally we would provide a way to configure global settings like SSH keys to use for hosts, number of Nginx servers per host, etc...

#
# @cli.command()
# async def add_host():
#     Ideally we would run the agent via ssh in a remote machine


if __name__ == "__main__":
    cli()
