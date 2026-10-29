"""Server OPC UA dimostrativo per le schermate della guida: otto segnali di impianto simulati (strumento di documentazione, non fa parte del prodotto)."""
import asyncio
import math
import socket
import time

from asyncua import Server, ua


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


SIGNALS = [
    # nome, funzione del tempo t (s), unità
    ('Pressione', lambda t: 100 + 60 * math.sin(t / 2.1) + 3 * math.sin(t * 7.3), 'bar'),
    ('Forza', lambda t: 450 + 250 * math.sin(t / 2.1 + 1.7) + 8 * math.sin(t * 5.1), 'kN'),
    ('Posizione', lambda t: 50 + 40 * math.sin(t / 2.1 + 3.4), 'mm'),
    ('Velocità', lambda t: 15 + 12 * math.sin(t / 2.1 + 5.1) + 0.4 * math.sin(t * 9.7), 'mm/s'),
    ('Marcia', lambda t: (int(t / 6) % 3) != 2, ''),
    ('Temperatura', lambda t: 180 + 12 * math.sin(t / 9) + 1.5 * math.sin(t * 3.1), '°C'),
    ('Setpoint', lambda t: 180 + 4 * (1 if int(t / 8) % 2 else -1), '°C'),
    ('Potenza', lambda t: 50 + 25 * math.sin(t / 3.3) + 2 * math.sin(t * 6.1), 'kW'),
]


async def start(port=None):
    port = port or free_port()
    server = Server()
    await server.init()
    endpoint = f'opc.tcp://127.0.0.1:{port}/demo'
    server.set_endpoint(endpoint)
    server.set_security_policy([ua.SecurityPolicyType.NoSecurity])
    ns = await server.register_namespace('urn:space-fieldbus-analyzer:demo')
    plant = await server.nodes.objects.add_object(ns, 'Impianto')
    nodes = []
    for name, _, unit in SIGNALS:
        node = await plant.add_variable(ns, name, False if name == 'Marcia' else 0.0)
        if unit:
            await node.add_property(ns, 'EngineeringUnit', unit)
        nodes.append(node)
    # Alcune variabili di scorta per rendere realistico l'elenco dopo la scansione.
    spare = await plant.add_object(ns, 'Diagnostica')
    for i in range(1, 13):
        await spare.add_variable(ns, f'Contatore {i:02d}', float(i))
    await server.start()
    stop = asyncio.Event()

    async def update():
        t0 = time.time()
        while not stop.is_set():
            t = time.time() - t0
            for node, (name, fn, _) in zip(nodes, SIGNALS):
                value = fn(t)
                await node.write_value(value if name == 'Marcia' else float(value))
            await asyncio.sleep(0.05)

    task = asyncio.create_task(update())

    async def close():
        stop.set()
        await task
        await server.stop()

    return endpoint, close


if __name__ == '__main__':
    async def main():
        endpoint, close = await start()
        print(endpoint, flush=True)
        await asyncio.sleep(3600)
    asyncio.run(main())
