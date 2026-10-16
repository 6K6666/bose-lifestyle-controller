"""LS50/M1 reported commands, RemoteCentral cnb post 23 (2020-09-30).
A/B/C controls exposed here have user-confirmed effects; BO is read-only.
Room D entries are inferred and guarded until commissioned.
Room volume indexes and source/mute masks below are explicitly published;
never derive one from the other. No service, memory, firmware or EEPROM commands.
"""
VOLUME_DOWN = b'MM 44,42,00\n'
COMMANDS = {'volume-down': VOLUME_DOWN, 'identify': b'BO\n',
            'system-power-on': b'MM 4a,41,01\n', 'system-power-off': b'MM 4a,43,01\n',
            'room-a-power-on': b'MM 4a,41,01\n', 'room-a-power-off': b'MM 4a,43,01\n'}
# (published A-C rooms; Room D index/mask are inferred and guarded)
for room, index, mask in (('a','00','01'),('b','01','02'),('c','02','04'),('d','03','08')):
    if room != 'a':
        COMMANDS[f'room-{room}-power-on'] = f'MM 4a,41,{mask}\n'.encode()
        COMMANDS[f'room-{room}-power-off'] = f'MM 4a,43,{mask}\n'.encode()
    COMMANDS[f'room-{room}-volume-up'] = f'MM 44,41,{index}\n'.encode()
    if room != 'a': COMMANDS[f'room-{room}-volume-down'] = f'MM 44,42,{index}\n'.encode()
    COMMANDS[f'room-{room}-mute'] = f'MM 43,41,{mask}\n'.encode()
    COMMANDS[f'room-{room}-unmute'] = f'MM 43,40,{mask}\n'.encode()
    for source, code in (('am','00'),('fm','01'),('cd','02'),('aux','03'),('video-1','04'),('video-2','05'),('tape','06')):
        COMMANDS[f'room-{room}-source-{source}'] = f'MM 42,40,{mask},{code}\n'.encode()
REFUSED_MESSAGE = 'refused by M1; no retry'

# Schema 4: every status action is a button except these. volume-down stays an entity (its unique ID backs dashboards).
SCHEMA4_EXCLUDED = frozenset({'identify', 'system-power-on', 'system-power-off'})
# Names for newly listed actions, built from status function/source. Room names come from the device, not the entity.
FUNCTION_NAMES = {'power-on': 'Power On', 'power-off': 'Power Off', 'volume-up': 'Volume Up', 'volume-down': 'Volume Down',
                  'mute': 'Mute', 'unmute': 'Unmute'}
SOURCE_NAMES = {'am': 'AM', 'fm': 'FM', 'cd': 'CD', 'aux': 'Aux', 'video-1': 'Video 1', 'video-2': 'Video 2', 'tape': 'Tape'}
