import sys; sys.path.insert(0, 'backend')
from ai.reharmonizer import Reharmonizer

rh = Reharmonizer(level=3)
test_chords = [
    {'time': 0, 'end': 1, 'chord': 'Dm'},
    {'time': 1, 'end': 3, 'chord': 'G7'},
    {'time': 3, 'end': 5, 'chord': 'C'},
    {'time': 5, 'end': 7, 'chord': 'Fmaj7'},
    {'time': 7, 'end': 9, 'chord': 'A#'},
    {'time': 9, 'end': 10, 'chord': 'E'},
    {'time': 10, 'end': 10.5, 'chord': 'E7'},
    {'time': 10.5, 'end': 14, 'chord': 'A'}
]
try:
    res = rh.jazzify(test_chords, key='D', mode='transformer')
    print('Returned chords:')
    for c in res['chords']:
        print(f"{c['time']}~{c['end']}: {c['chord']}")
except Exception as e:
    import traceback
    traceback.print_exc()
