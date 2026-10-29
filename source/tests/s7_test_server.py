"""Snap7 S7 simulator for the C# application tests (test tooling only; pip install python-snap7==2.0.2)."""
import ctypes
import socket
import struct
from snap7.server import Server
from snap7.type import SrvArea


def start_server():
    server=Server(log=False)
    memory=bytearray(2048)
    struct.pack_into('>f',memory,0,42.5)
    struct.pack_into('>h',memory,4,-1234)
    memory[6]=0x80
    struct.pack_into('>q',memory,300,1792585845123456)   # PLC clock tag: 2026-10-21T12:30:45.123456Z in microseconds
    struct.pack_into('>d',memory,8,12.125)
    struct.pack_into('>q',memory,16,-9223372036854775807)
    memory[32:32+256]=bytes([254,3])+b'ABC'+bytes(251)
    server.test_buffers=[]
    for area,index in ((SrvArea.DB,1),(SrvArea.MK,0),(SrvArea.PE,0),(SrvArea.PA,0)):
        buffer=(ctypes.c_uint8*len(memory)).from_buffer_copy(memory)
        server.test_buffers.append(buffer);server.register_area(area,index,buffer)
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1',0));port=reservation.getsockname()[1]
    server.start_to('127.0.0.1',port)
    return server,port
