from pymobiledevice3.cli.remote import install_driver_if_required, get_device_list
from pymobiledevice3.remote.module_imports import start_tunnel
from pymobiledevice3.remote.module_imports import verify_tunnel_imports
from pymobiledevice3.remote.remote_service_discovery import RemoteServiceDiscoveryService
from pymobiledevice3.services.dvt.dvt_secure_socket_proxy import DvtSecureSocketProxyService
from pymobiledevice3.services.dvt.instruments.location_simulation import LocationSimulation
from pymobiledevice3.services.amfi import AmfiService
from pymobiledevice3.lockdown import create_using_usbmux
from pymobiledevice3.exceptions import AmfiError, DeveloperModeIsNotEnabledError

import asyncio
import eventlet
import socketio
from multiprocessing import Process
import os


def server(tunnel_host, tunnel_port):
    clients = {}
    sio = socketio.Server(cors_allowed_origins='*')
    app = socketio.WSGIApp(sio, static_files={
        '/': os.path.join(os.path.dirname(__file__), 'index.html'),
        '/index.js': os.path.join(os.path.dirname(__file__), 'index.js'),
        '/main.css': os.path.join(os.path.dirname(__file__), 'main.css'),
    })

    @sio.event
    def connect(sid, environ):
        rsd = RemoteServiceDiscoveryService((tunnel_host, tunnel_port))
        rsd.connect()
        dvt = DvtSecureSocketProxyService(rsd)
        dvt.perform_handshake()
        loc = LocationSimulation(dvt)
        clients[sid] = [rsd, loc]

    @sio.event
    def location(sid, data):
        la, lo = list(map(lambda x: float(x), data.split(',')))
        clients[sid][1].set(la, lo)

    @sio.event
    def disconnect(sid):
        clients[sid][1].clear()
        clients[sid][0].service.close()
        clients.pop(sid)

    s = eventlet.listen(('localhost', 3000))
    [ip, port] = s.getsockname()
    print('--port', port)
    eventlet.wsgi.server(s, app)


async def start_quic_tunnel(service_provider: RemoteServiceDiscoveryService) -> None:
    if start_tunnel is None:
        raise NotImplementedError('failed to start the QUIC tunnel on your platform')
    async with start_tunnel(service_provider) as tunnel_result:
        print('UDID:', service_provider.udid)
        print('ProductType:', service_provider.product_type)
        print('ProductVersion:', service_provider.product_version)
        print('Interface:', tunnel_result.interface)
        print('--rsd', tunnel_result.address, tunnel_result.port)

        ui = Process(target=server, args=(tunnel_result.address, tunnel_result.port))
        ui.start()

        while True:
            await asyncio.sleep(.5)


def create_tunnel():
    """ start quic tunnel """
    install_driver_if_required()
    if not verify_tunnel_imports():
        return
    devices = get_device_list()
    if not devices:
        # no devices were found
        raise Exception('NoDeviceConnectedError')
    if len(devices) == 1:
        # only one device found
        rsd = devices[0]
    else:
        # several devices were found
        raise Exception('TooManyDevicesConnectedError')
    
    lockdown = create_using_usbmux(rsd.udid)
    if not lockdown.developer_mode_status:
        service = lockdown.start_lockdown_service(AmfiService.SERVICE_NAME)
        resp = service.send_recv_plist({'action': 0})
        error = resp.get('Error')
        if error is not None:
            raise AmfiError(error)
        print('')
        raise DeveloperModeIsNotEnabledError('need to enable developer mode toggle')

    asyncio.run(start_quic_tunnel(rsd))


if __name__ == '__main__':
    try:
        create_tunnel()
    except KeyboardInterrupt:
        pass
