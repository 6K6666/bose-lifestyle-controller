#!/usr/bin/env python3
"""Bridge room A volume-down; direct loopback requires Bose disconnected."""

import argparse

from bose_commands import DEFAULT_PORT, DEFAULT_BAUD, VOLUME_DOWN


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('loopback', 'volume-down'), help='volume-down targets room A only')
    parser.add_argument('--port', default=DEFAULT_PORT)
    parser.add_argument('--baud', type=int, choices=(1200, 4800), default=DEFAULT_BAUD)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--live-loopback', action='store_true', help='diagnostic only; Bose must be disconnected')
    args = parser.parse_args()
    command = VOLUME_DOWN if args.action == 'volume-down' else b'BOSE\r'
    print(f'TX {command!r}: {command.hex(" ")}')
    if args.action == 'volume-down' and (args.port != DEFAULT_PORT or args.baud != DEFAULT_BAUD or args.live_loopback):
        parser.error('port/baud/live-loopback overrides are for diagnostic loopback only')
    if args.dry_run or (args.action == 'loopback' and not args.live_loopback):
        print(f'Dry run: {args.port}, {args.baud} baud, 8N1, no flow control; no port opened')
        return

    if args.action == 'volume-down':
        from bose_request import request, SOCKET
        try: reply = request(SOCKET)
        except OSError as error: raise SystemExit('Bridge unavailable; no retry') from error
        print(reply)
        if reply in ('rejected', 'uncertain; no retry'): raise SystemExit(1)
        return

    import serial
    try:
        port = serial.Serial(port=None, baudrate=args.baud, bytesize=8, parity='N', stopbits=1,
                             timeout=2, write_timeout=2, xonxoff=False, rtscts=False,
                             dsrdtr=False, exclusive=True)
        port.dtr = False
        port.rts = False
        port.port = args.port
        with port:
            before = port.read(512)
            if before:
                raise SystemExit(f'Stopped before transmission: unsolicited RX {before.hex(" ")} {before!r}')
            if port.write(command) != len(command):
                raise SystemExit('Partial write; stopped without retry')
            port.flush()
            received = port.read(len(command) if args.action == 'loopback' else 512)
            print(f'RX {received.hex(" ")} {received!r}')
            if args.action == 'loopback':
                if received != command:
                    raise SystemExit('Loopback failed')
                print('Loopback passed')
            else:
                print('Transmitted one room A volume-down; control requires P1 confirmation. No retry.')
    except (serial.SerialException, OSError) as error:
        raise SystemExit(f'Serial error: {error}; no retry') from error


if __name__ == '__main__':
    main()
