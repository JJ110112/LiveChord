"""
和弦圖資料庫 — 支援多樂器 (guitar, ukulele, ...)
每個和弦包含: strings (按法), baseFret (起始格), barres (封弦格數列表)
strings: 從低音弦到高音弦, -1=悶音(X), 0=空弦(O), 1~=品格位置
"""

from instrument_registry import get_instrument, list_instruments

# 吉他和弦 (6弦, EADGBE)
GUITAR_CHORDS = {
    # === C 系列 ===
    'C':       {'strings': [-1, 3, 2, 0, 1, 0], 'baseFret': 1, 'barres': [], 'fingers': [0, 3, 2, 0, 1, 0]},
    'Cm':      {'strings': [-1, 3, 5, 5, 4, 3], 'baseFret': 1, 'barres': [3], 'fingers': [0, 1, 3, 4, 2, 1]},
    'C7':      {'strings': [-1, 3, 2, 3, 1, 0], 'baseFret': 1, 'barres': [], 'fingers': [0, 3, 2, 4, 1, 0]},
    'Cmaj7':   {'strings': [-1, 3, 2, 0, 0, 0], 'baseFret': 1, 'barres': [], 'fingers': [0, 2, 1, 0, 0, 0]},
    'Cm7':     {'strings': [-1, 1, 3, 1, 2, 1], 'baseFret': 3, 'barres': [1], 'fingers': [0, 1, 3, 1, 2, 1]},
    'Cdim':    {'strings': [-1, 3, 4, 5, 4, -1], 'baseFret': 1, 'barres': [], 'fingers': [0, 1, 2, 4, 3, 0]},
    'Cdim7':   {'strings': [-1, 3, 4, 2, 4, 2], 'baseFret': 1, 'barres': [], 'fingers': [0, 3, 4, 1, 4, 2]},
    'Caug':    {'strings': [-1, 3, 2, 1, 1, 0], 'baseFret': 1, 'barres': [], 'fingers': [0, 4, 3, 1, 2, 0]},
    'Csus2':   {'strings': [-1, 3, 3, 0, 1, 3], 'baseFret': 1, 'barres': [], 'fingers': [0, 2, 3, 0, 1, 4]},
    'Csus4':   {'strings': [-1, 3, 3, 0, 1, 1], 'baseFret': 1, 'barres': [], 'fingers': [0, 3, 4, 0, 1, 2]},
    'C6':      {'strings': [-1, 0, 2, 2, 1, 3], 'baseFret': 1, 'barres': [], 'fingers': [0, 0, 2, 3, 1, 4]},
    'Cm6':     {'strings': [-1, -1, 1, 2, 1, 3], 'baseFret': 1, 'barres': [], 'fingers': [0, 0, 1, 3, 2, 4]},
    'C9':      {'strings': [-1, 3, 2, 3, 3, 0], 'baseFret': 1, 'barres': [], 'fingers': [0, 2, 1, 3, 4, 0]},
    'C6/9':    {'strings': [-1, 3, 2, 2, 3, 0], 'baseFret': 1, 'barres': [], 'fingers': [0, 3, 1, 2, 4, 0]},
    'Cadd9':   {'strings': [-1, 3, 2, 0, 3, 0], 'baseFret': 1, 'barres': [], 'fingers': [0, 2, 1, 0, 3, 0]},

    # === C#/Db 系列 ===
    'C#':      {'strings': [-1, 4, 3, 1, 2, 1], 'baseFret': 1, 'barres': [1], 'fingers': [0, 4, 3, 1, 2, 1]},
    'Db':      {'strings': [-1, 4, 3, 1, 2, 1], 'baseFret': 1, 'barres': [1], 'fingers': [0, 4, 3, 1, 2, 1]},
    'C#m':     {'strings': [-1, 4, 6, 6, 5, 4], 'baseFret': 1, 'barres': [4], 'fingers': [0, 1, 3, 4, 2, 1]},
    'Dbm':     {'strings': [-1, 4, 6, 6, 5, 4], 'baseFret': 1, 'barres': [4], 'fingers': [0, 1, 3, 4, 2, 1]},
    'C#7':     {'strings': [-1, 4, 3, 4, 2, -1], 'baseFret': 1, 'barres': [], 'fingers': [0, 3, 2, 4, 1, 0]},
    'Db7':     {'strings': [-1, 4, 3, 4, 2, -1], 'baseFret': 1, 'barres': [], 'fingers': [0, 3, 2, 4, 1, 0]},
    'C#maj7':  {'strings': [-1, 4, 3, 1, 1, 1], 'baseFret': 1, 'barres': [1], 'fingers': [0, 3, 2, 1, 1, 1]},
    'Dbmaj7':  {'strings': [-1, 4, 3, 1, 1, 1], 'baseFret': 1, 'barres': [1], 'fingers': [0, 3, 2, 1, 1, 1]},
    'C#m7':    {'strings': [-1, 4, 2, 4, 2, 4], 'baseFret': 1, 'barres': [], 'fingers': [0, 3, 1, 4, 2, 4]},
    'Dbm7':    {'strings': [-1, 4, 2, 4, 2, 4], 'baseFret': 1, 'barres': [], 'fingers': [0, 3, 1, 4, 2, 4]},
    'C#dim':   {'strings': [-1, 4, 5, 6, 5, -1], 'baseFret': 1, 'barres': [], 'fingers': [0, 1, 2, 4, 3, 0]},
    'C#dim7':  {'strings': [-1, 4, 5, 3, 5, 3], 'baseFret': 1, 'barres': [], 'fingers': [0, 3, 4, 1, 4, 2]},
    'C#aug':   {'strings': [-1, 4, 3, 2, 2, 1], 'baseFret': 1, 'barres': [], 'fingers': [0, 4, 4, 2, 3, 1]},
    'C#sus4':  {'strings': [-1, 4, 4, 1, 2, 2], 'baseFret': 1, 'barres': [], 'fingers': [0, 4, 4, 1, 2, 3]},

    # === D 系列 ===
    'D':       {'strings': [-1, -1, 0, 2, 3, 2], 'baseFret': 1, 'barres': [], 'fingers': [0, 0, 0, 1, 3, 2]},
    'Dm':      {'strings': [-1, -1, 0, 2, 3, 1], 'baseFret': 1, 'barres': [], 'fingers': [0, 0, 0, 2, 3, 1]},
    'D7':      {'strings': [-1, -1, 0, 2, 1, 2], 'baseFret': 1, 'barres': [], 'fingers': [0, 0, 0, 2, 1, 3]},
    'Dmaj7':   {'strings': [-1, -1, 0, 2, 2, 2], 'baseFret': 1, 'barres': [], 'fingers': [0, 0, 0, 1, 2, 3]},
    'Dm7':     {'strings': [-1, -1, 0, 2, 1, 1], 'baseFret': 1, 'barres': [], 'fingers': [0, 0, 0, 3, 1, 2]},
    'Ddim':    {'strings': [-1, -1, 0, 1, 3, 1], 'baseFret': 1, 'barres': [], 'fingers': [0, 0, 0, 1, 3, 2]},
    'Ddim7':   {'strings': [-1, -1, 0, 1, 0, 1], 'baseFret': 1, 'barres': [], 'fingers': [0, 0, 0, 1, 0, 2]},
    'Daug':    {'strings': [-1, -1, 0, 3, 3, 2], 'baseFret': 1, 'barres': [], 'fingers': [0, 0, 0, 2, 3, 1]},
    'Dsus2':   {'strings': [-1, -1, 0, 2, 3, 0], 'baseFret': 1, 'barres': [], 'fingers': [0, 0, 0, 1, 2, 0]},
    'Dsus4':   {'strings': [-1, -1, 0, 2, 3, 3], 'baseFret': 1, 'barres': [], 'fingers': [0, 0, 0, 1, 2, 3]},
    'D6':      {'strings': [-1, -1, 0, 2, 0, 2], 'baseFret': 1, 'barres': [], 'fingers': [0, 0, 0, 1, 0, 2]},
    'Dm6':     {'strings': [-1, -1, 0, 2, 0, 1], 'baseFret': 1, 'barres': [], 'fingers': [0, 0, 0, 2, 0, 1]},
    'D9':      {'strings': [-1, -1, 0, 2, 1, 0], 'baseFret': 1, 'barres': [], 'fingers': [0, 0, 0, 2, 1, 0]},
    'D6/9':    {'strings': [2, 0, 0, 2, 0, 0], 'baseFret': 1, 'barres': [], 'fingers': [1, 0, 0, 2, 0, 0]},
    'Dadd9':   {'strings': [-1, -1, 0, 2, 3, 0], 'baseFret': 1, 'barres': [], 'fingers': [0, 0, 0, 1, 2, 0]},

    # === D#/Eb 系列 ===
    'Eb':      {'strings': [-1, -1, 1, 3, 4, 3], 'baseFret': 1, 'barres': [], 'fingers': [0, 0, 1, 2, 4, 3]},
    'D#':      {'strings': [-1, -1, 1, 3, 4, 3], 'baseFret': 1, 'barres': [], 'fingers': [0, 0, 1, 2, 4, 3]},
    'Ebm':     {'strings': [-1, -1, 1, 3, 4, 2], 'baseFret': 1, 'barres': [], 'fingers': [0, 0, 1, 3, 4, 2]},
    'D#m':     {'strings': [-1, -1, 1, 3, 4, 2], 'baseFret': 1, 'barres': [], 'fingers': [0, 0, 1, 3, 4, 2]},
    'Eb7':     {'strings': [-1, -1, 1, 3, 2, 3], 'baseFret': 1, 'barres': [], 'fingers': [0, 0, 1, 3, 2, 4]},
    'Ebmaj7':  {'strings': [-1, -1, 1, 3, 3, 3], 'baseFret': 1, 'barres': [], 'fingers': [0, 0, 1, 2, 3, 4]},
    'Ebm7':    {'strings': [-1, -1, 1, 3, 2, 2], 'baseFret': 1, 'barres': [], 'fingers': [0, 0, 1, 4, 2, 3]},
    'Ebdim':   {'strings': [-1, -1, 1, 2, 4, 2], 'baseFret': 1, 'barres': [], 'fingers': [0, 0, 1, 2, 4, 3]},
    'Ebdim7':  {'strings': [-1, -1, 1, 2, 1, 2], 'baseFret': 1, 'barres': [], 'fingers': [0, 0, 1, 3, 2, 4]},
    'Ebaug':   {'strings': [-1, -1, 1, 0, 0, 3], 'baseFret': 1, 'barres': [], 'fingers': [0, 0, 1, 0, 0, 2]},
    'Ebsus4':  {'strings': [-1, -1, 1, 3, 4, 4], 'baseFret': 1, 'barres': [], 'fingers': [0, 0, 1, 2, 3, 4]},

    # === E 系列 ===
    'E':       {'strings': [0, 2, 2, 1, 0, 0], 'baseFret': 1, 'barres': [], 'fingers': [0, 2, 3, 1, 0, 0]},
    'Em':      {'strings': [0, 2, 2, 0, 0, 0], 'baseFret': 1, 'barres': [], 'fingers': [0, 1, 2, 0, 0, 0]},
    'E7':      {'strings': [0, 2, 0, 1, 0, 0], 'baseFret': 1, 'barres': [], 'fingers': [0, 2, 0, 1, 0, 0]},
    'Emaj7':   {'strings': [0, 2, 1, 1, 0, 0], 'baseFret': 1, 'barres': [], 'fingers': [0, 3, 1, 2, 0, 0]},
    'Em7':     {'strings': [0, 2, 0, 0, 0, 0], 'baseFret': 1, 'barres': [], 'fingers': [0, 1, 0, 0, 0, 0]},
    'Edim':    {'strings': [0, 1, 2, 0, -1, -1], 'baseFret': 1, 'barres': [], 'fingers': [0, 1, 2, 0, 0, 0]},
    'Edim7':   {'strings': [-1, 1, 2, 0, 2, 0], 'baseFret': 1, 'barres': [], 'fingers': [0, 1, 2, 0, 3, 0]},
    'Eaug':    {'strings': [0, 3, 2, 1, 1, 0], 'baseFret': 1, 'barres': [], 'fingers': [0, 4, 3, 1, 2, 0]},
    'Esus2':   {'strings': [0, 2, 4, 4, 0, 0], 'baseFret': 1, 'barres': [], 'fingers': [0, 1, 2, 3, 0, 0]},
    'Esus4':   {'strings': [0, 2, 2, 2, 0, 0], 'baseFret': 1, 'barres': [], 'fingers': [0, 1, 2, 3, 0, 0]},
    'E6':      {'strings': [0, 2, 2, 1, 2, 0], 'baseFret': 1, 'barres': [], 'fingers': [0, 2, 3, 1, 4, 0]},
    'Em6':     {'strings': [0, 2, 2, 0, 2, 0], 'baseFret': 1, 'barres': [], 'fingers': [0, 1, 2, 0, 3, 0]},
    'E9':      {'strings': [0, 2, 0, 1, 0, 2], 'baseFret': 1, 'barres': [], 'fingers': [0, 2, 0, 1, 0, 3]},
    'Eadd9':   {'strings': [0, 2, 2, 1, 0, 2], 'baseFret': 1, 'barres': [], 'fingers': [0, 2, 3, 1, 0, 4]},

    # === F 系列 ===
    'F':       {'strings': [1, 1, 2, 3, 3, 1], 'baseFret': 1, 'barres': [1], 'fingers': [1, 1, 2, 3, 4, 1]},
    'Fm':      {'strings': [1, 1, 3, 3, 1, 1], 'baseFret': 1, 'barres': [1], 'fingers': [1, 1, 2, 3, 1, 1]},
    'F7':      {'strings': [1, 1, 2, 1, 3, 1], 'baseFret': 1, 'barres': [1], 'fingers': [1, 1, 2, 1, 3, 1]},
    'Fmaj7':   {'strings': [-1, -1, 3, 2, 1, 0], 'baseFret': 1, 'barres': [], 'fingers': [0, 0, 3, 2, 1, 0]},
    'Fm7':     {'strings': [1, 1, 1, 1, 1, 1], 'baseFret': 1, 'barres': [1], 'fingers': [1, 1, 1, 1, 1, 1]},
    'Fdim':    {'strings': [-1, -1, 3, 1, 0, 1], 'baseFret': 1, 'barres': [], 'fingers': [0, 0, 3, 1, 0, 2]},
    'Fdim7':   {'strings': [-1, 2, 3, 1, 3, 1], 'baseFret': 1, 'barres': [], 'fingers': [0, 3, 4, 1, 4, 2]},
    'Faug':    {'strings': [-1, -1, 3, 2, 2, 1], 'baseFret': 1, 'barres': [], 'fingers': [0, 0, 4, 2, 3, 1]},
    'Fsus2':   {'strings': [-1, -1, 3, 0, 1, 1], 'baseFret': 1, 'barres': [], 'fingers': [0, 0, 3, 0, 1, 2]},
    'Fsus4':   {'strings': [1, 1, 3, 3, 1, 1], 'baseFret': 1, 'barres': [1], 'fingers': [1, 1, 2, 3, 1, 1]},
    'F6':      {'strings': [1, -1, 0, 2, 1, 1], 'baseFret': 1, 'barres': [], 'fingers': [1, 0, 0, 4, 2, 3]},
    'Fm6':     {'strings': [-1, -1, 0, 1, 1, 1], 'baseFret': 1, 'barres': [], 'fingers': [0, 0, 0, 1, 2, 3]},
    'F9':      {'strings': [1, 0, 1, 0, 1, -1], 'baseFret': 1, 'barres': [], 'fingers': [1, 0, 2, 0, 3, 0]},
    'Fadd9':   {'strings': [-1, -1, 3, 2, 1, 3], 'baseFret': 1, 'barres': [], 'fingers': [0, 0, 3, 2, 1, 4]},

    # === F#/Gb 系列 ===
    'F#':      {'strings': [2, 4, 4, 3, 2, 2], 'baseFret': 1, 'barres': [2], 'fingers': [1, 3, 4, 2, 1, 1]},
    'Gb':      {'strings': [2, 4, 4, 3, 2, 2], 'baseFret': 1, 'barres': [2], 'fingers': [1, 3, 4, 2, 1, 1]},
    'F#m':     {'strings': [2, 4, 4, 2, 2, 2], 'baseFret': 1, 'barres': [2], 'fingers': [1, 2, 3, 1, 1, 1]},
    'Gbm':     {'strings': [2, 4, 4, 2, 2, 2], 'baseFret': 1, 'barres': [2], 'fingers': [1, 2, 3, 1, 1, 1]},
    'F#7':     {'strings': [2, 4, 2, 3, 2, -1], 'baseFret': 1, 'barres': [], 'fingers': [1, 4, 2, 4, 3, 0]},
    'Gb7':     {'strings': [2, 4, 2, 3, 2, -1], 'baseFret': 1, 'barres': [], 'fingers': [1, 4, 2, 4, 3, 0]},
    'F#maj7':  {'strings': [-1, -1, 4, 3, 2, 1], 'baseFret': 1, 'barres': [], 'fingers': [0, 0, 4, 3, 2, 1]},
    'Gbmaj7':  {'strings': [-1, -1, 4, 3, 2, 1], 'baseFret': 1, 'barres': [], 'fingers': [0, 0, 4, 3, 2, 1]},
    'F#m7':    {'strings': [2, 0, 2, 2, 2, 0], 'baseFret': 1, 'barres': [], 'fingers': [1, 0, 2, 3, 4, 0]},
    'Gbm7':    {'strings': [2, 0, 2, 2, 2, 0], 'baseFret': 1, 'barres': [], 'fingers': [1, 0, 2, 3, 4, 0]},
    'F#dim':   {'strings': [-1, -1, 4, 2, 1, 2], 'baseFret': 1, 'barres': [], 'fingers': [0, 0, 4, 2, 1, 3]},
    'F#dim7':  {'strings': [-1, -1, 1, 2, 1, 2], 'baseFret': 1, 'barres': [], 'fingers': [0, 0, 1, 3, 2, 4]},
    'F#aug':   {'strings': [-1, -1, 4, 3, 3, 2], 'baseFret': 1, 'barres': [], 'fingers': [0, 0, 4, 2, 3, 1]},
    'F#sus4':  {'strings': [2, 4, 4, 4, 2, 2], 'baseFret': 1, 'barres': [2], 'fingers': [1, 2, 3, 4, 1, 1]},

    # === G 系列 ===
    'G':       {'strings': [3, 2, 0, 0, 0, 3], 'baseFret': 1, 'barres': [], 'fingers': [2, 1, 0, 0, 0, 3]},
    'Gm':      {'strings': [3, 5, 5, 3, 3, 3], 'baseFret': 1, 'barres': [3], 'fingers': [1, 2, 3, 1, 1, 1]},
    'G7':      {'strings': [3, 2, 0, 0, 0, 1], 'baseFret': 1, 'barres': [], 'fingers': [3, 2, 0, 0, 0, 1]},
    'Gmaj7':   {'strings': [3, 2, 0, 0, 0, 2], 'baseFret': 1, 'barres': [], 'fingers': [3, 1, 0, 0, 0, 2]},
    'Gm7':     {'strings': [3, 5, 3, 3, 3, 3], 'baseFret': 1, 'barres': [3], 'fingers': [1, 2, 1, 1, 1, 1]},
    'Gdim':    {'strings': [3, 4, 5, 3, -1, -1], 'baseFret': 1, 'barres': [], 'fingers': [1, 3, 4, 2, 0, 0]},
    'Gdim7':   {'strings': [-1, -1, 2, 3, 2, 3], 'baseFret': 1, 'barres': [], 'fingers': [0, 0, 1, 3, 2, 4]},
    'Gaug':    {'strings': [3, 2, 1, 0, 0, 3], 'baseFret': 1, 'barres': [], 'fingers': [3, 2, 1, 0, 0, 4]},
    'Gsus2':   {'strings': [3, 0, 0, 0, 3, 3], 'baseFret': 1, 'barres': [], 'fingers': [1, 0, 0, 0, 2, 3]},
    'Gsus4':   {'strings': [3, 5, 5, 5, 3, 3], 'baseFret': 1, 'barres': [3], 'fingers': [1, 2, 3, 4, 1, 1]},
    'G6':      {'strings': [3, 2, 0, 0, 0, 0], 'baseFret': 1, 'barres': [], 'fingers': [2, 1, 0, 0, 0, 0]},
    'Gm6':     {'strings': [-1, -1, 5, 3, 3, 0], 'baseFret': 1, 'barres': [], 'fingers': [0, 0, 3, 1, 2, 0]},
    'G9':      {'strings': [3, 0, 0, 0, 0, 1], 'baseFret': 1, 'barres': [], 'fingers': [2, 0, 0, 0, 0, 1]},
    'Gadd9':   {'strings': [3, 0, 0, 0, 0, 3], 'baseFret': 1, 'barres': [], 'fingers': [1, 0, 0, 0, 0, 2]},

    # === G#/Ab 系列 ===
    'Ab':      {'strings': [4, 6, 6, 5, 4, 4], 'baseFret': 1, 'barres': [4], 'fingers': [1, 3, 4, 2, 1, 1]},
    'G#':      {'strings': [4, 6, 6, 5, 4, 4], 'baseFret': 1, 'barres': [4], 'fingers': [1, 3, 4, 2, 1, 1]},
    'Abm':     {'strings': [4, 6, 6, 4, 4, 4], 'baseFret': 1, 'barres': [4], 'fingers': [1, 2, 3, 1, 1, 1]},
    'G#m':     {'strings': [4, 6, 6, 4, 4, 4], 'baseFret': 1, 'barres': [4], 'fingers': [1, 2, 3, 1, 1, 1]},
    'Ab7':     {'strings': [-1, -1, 1, 1, 1, 2], 'baseFret': 4, 'barres': [1], 'fingers': [0, 0, 1, 1, 1, 2]},
    'G#7':     {'strings': [-1, -1, 1, 1, 1, 2], 'baseFret': 4, 'barres': [1], 'fingers': [0, 0, 1, 1, 1, 2]},
    'Abmaj7':  {'strings': [4, 6, 5, 5, 4, 4], 'baseFret': 1, 'barres': [4], 'fingers': [1, 4, 2, 3, 1, 1]},
    'G#maj7':  {'strings': [4, 6, 5, 5, 4, 4], 'baseFret': 1, 'barres': [4], 'fingers': [1, 4, 2, 3, 1, 1]},
    'Abm7':    {'strings': [-1, -1, 1, 1, 0, 2], 'baseFret': 4, 'barres': [], 'fingers': [0, 0, 1, 2, 0, 3]},
    'G#m7':    {'strings': [-1, -1, 1, 1, 0, 2], 'baseFret': 4, 'barres': [], 'fingers': [0, 0, 1, 2, 0, 3]},
    'Abdim':   {'strings': [4, 5, 6, 4, -1, -1], 'baseFret': 1, 'barres': [], 'fingers': [1, 3, 4, 2, 0, 0]},
    'Abdim7':  {'strings': [-1, -1, 0, 1, 0, 1], 'baseFret': 1, 'barres': [], 'fingers': [0, 0, 0, 1, 0, 2]},
    'Abaug':   {'strings': [-1, -1, 2, 1, 1, 0], 'baseFret': 1, 'barres': [], 'fingers': [0, 0, 3, 1, 2, 0]},
    'Absus4':  {'strings': [4, 6, 6, 6, 4, 4], 'baseFret': 1, 'barres': [4], 'fingers': [1, 2, 3, 4, 1, 1]},

    # === A 系列 ===
    'A':       {'strings': [-1, 0, 2, 2, 2, 0], 'baseFret': 1, 'barres': [], 'fingers': [0, 0, 1, 2, 3, 0]},
    'Am':      {'strings': [-1, 0, 2, 2, 1, 0], 'baseFret': 1, 'barres': [], 'fingers': [0, 0, 2, 3, 1, 0]},
    'A7':      {'strings': [-1, 0, 2, 0, 2, 0], 'baseFret': 1, 'barres': [], 'fingers': [0, 0, 1, 0, 2, 0]},
    'Amaj7':   {'strings': [-1, 0, 2, 1, 2, 0], 'baseFret': 1, 'barres': [], 'fingers': [0, 0, 2, 1, 3, 0]},
    'Am7':     {'strings': [-1, 0, 2, 0, 1, 0], 'baseFret': 1, 'barres': [], 'fingers': [0, 0, 2, 0, 1, 0]},
    'Adim':    {'strings': [-1, 0, 1, 2, 1, -1], 'baseFret': 1, 'barres': [], 'fingers': [0, 0, 1, 3, 2, 0]},
    'Adim7':   {'strings': [-1, 0, 1, 2, 1, 2], 'baseFret': 1, 'barres': [], 'fingers': [0, 0, 1, 3, 2, 4]},
    'Aaug':    {'strings': [-1, 0, 3, 2, 2, 1], 'baseFret': 1, 'barres': [], 'fingers': [0, 0, 4, 2, 3, 1]},
    'Asus2':   {'strings': [-1, 0, 2, 2, 0, 0], 'baseFret': 1, 'barres': [], 'fingers': [0, 0, 1, 2, 0, 0]},
    'Asus4':   {'strings': [-1, 0, 2, 2, 3, 0], 'baseFret': 1, 'barres': [], 'fingers': [0, 0, 1, 2, 3, 0]},
    'A6':      {'strings': [-1, 0, 2, 2, 2, 2], 'baseFret': 1, 'barres': [], 'fingers': [0, 0, 1, 2, 3, 4]},
    'Am6':     {'strings': [-1, 0, 2, 2, 1, 2], 'baseFret': 1, 'barres': [], 'fingers': [0, 0, 2, 3, 1, 4]},
    'A9':      {'strings': [-1, 0, 2, 4, 2, 3], 'baseFret': 1, 'barres': [], 'fingers': [0, 0, 1, 4, 2, 3]},
    'A6/9':    {'strings': [-1, 0, 4, 4, 2, 2], 'baseFret': 1, 'barres': [], 'fingers': [0, 0, 3, 4, 1, 2]},
    'Aadd9':   {'strings': [-1, 0, 2, 2, 2, 2], 'baseFret': 1, 'barres': [], 'fingers': [0, 0, 1, 2, 3, 4]},

    # === A#/Bb 系列 ===
    'Bb':      {'strings': [-1, 1, 3, 3, 3, 1], 'baseFret': 1, 'barres': [1], 'fingers': [0, 1, 2, 3, 4, 1]},
    'A#':      {'strings': [-1, 1, 3, 3, 3, 1], 'baseFret': 1, 'barres': [1], 'fingers': [0, 1, 2, 3, 4, 1]},
    'Bbm':     {'strings': [-1, 1, 3, 3, 2, 1], 'baseFret': 1, 'barres': [1], 'fingers': [0, 1, 3, 4, 2, 1]},
    'A#m':     {'strings': [-1, 1, 3, 3, 2, 1], 'baseFret': 1, 'barres': [1], 'fingers': [0, 1, 3, 4, 2, 1]},
    'Bb7':     {'strings': [-1, 1, 3, 1, 3, 1], 'baseFret': 1, 'barres': [1], 'fingers': [0, 1, 2, 1, 3, 1]},
    'Bbmaj7':  {'strings': [-1, 1, 3, 2, 3, 1], 'baseFret': 1, 'barres': [1], 'fingers': [0, 1, 3, 2, 4, 1]},
    'Bbm7':    {'strings': [-1, 1, 3, 1, 2, 1], 'baseFret': 1, 'barres': [1], 'fingers': [0, 1, 3, 1, 2, 1]},
    'Bbdim':   {'strings': [-1, 1, 2, 3, 2, -1], 'baseFret': 1, 'barres': [], 'fingers': [0, 1, 2, 4, 3, 0]},
    'Bbdim7':  {'strings': [-1, -1, 2, 3, 2, 3], 'baseFret': 1, 'barres': [], 'fingers': [0, 0, 1, 3, 2, 4]},
    'Bbaug':   {'strings': [-1, 1, 0, 3, 3, 2], 'baseFret': 1, 'barres': [], 'fingers': [0, 1, 0, 3, 4, 2]},
    'Bbsus2':  {'strings': [-1, 1, 3, 3, 1, 1], 'baseFret': 1, 'barres': [1], 'fingers': [0, 1, 2, 3, 1, 1]},
    'Bbsus4':  {'strings': [-1, 1, 3, 3, 4, 1], 'baseFret': 1, 'barres': [1], 'fingers': [0, 1, 2, 3, 4, 1]},
    'Bb6':     {'strings': [-1, 1, 3, 3, 3, 3], 'baseFret': 1, 'barres': [], 'fingers': [0, 1, 2, 3, 4, 4]},
    'Bb9':     {'strings': [-1, 1, 0, 1, 1, 1], 'baseFret': 1, 'barres': [], 'fingers': [0, 1, 0, 2, 3, 4]},
    'Bb11':    {'strings': [-1, 1, 1, 1, 3, 1], 'baseFret': 1, 'barres': [1], 'fingers': [0, 1, 1, 1, 2, 1]},

    # === B 系列 ===
    'B':       {'strings': [-1, 2, 4, 4, 4, 2], 'baseFret': 1, 'barres': [2], 'fingers': [0, 1, 2, 3, 4, 1]},
    'Bm':      {'strings': [-1, 2, 4, 4, 3, 2], 'baseFret': 1, 'barres': [2], 'fingers': [0, 1, 3, 4, 2, 1]},
    'B7':      {'strings': [-1, 2, 1, 2, 0, 2], 'baseFret': 1, 'barres': [], 'fingers': [0, 2, 1, 3, 0, 4]},
    'Bmaj7':   {'strings': [-1, 2, 4, 3, 4, 2], 'baseFret': 1, 'barres': [2], 'fingers': [0, 1, 3, 2, 4, 1]},
    'Bm7':     {'strings': [-1, 2, 0, 2, 0, 2], 'baseFret': 1, 'barres': [], 'fingers': [0, 1, 0, 2, 0, 3]},
    'Bdim':    {'strings': [-1, 2, 3, 4, 3, -1], 'baseFret': 1, 'barres': [], 'fingers': [0, 1, 2, 4, 3, 0]},
    'Bdim7':   {'strings': [-1, 2, 3, 1, 3, 1], 'baseFret': 1, 'barres': [], 'fingers': [0, 3, 4, 1, 4, 2]},
    'Baug':    {'strings': [-1, 2, 1, 0, 0, 3], 'baseFret': 1, 'barres': [], 'fingers': [0, 2, 1, 0, 0, 3]},
    'Bsus2':   {'strings': [-1, 2, 4, 4, 2, 2], 'baseFret': 1, 'barres': [2], 'fingers': [0, 1, 2, 3, 1, 1]},
    'Bsus4':   {'strings': [-1, 2, 4, 4, 5, 2], 'baseFret': 1, 'barres': [2], 'fingers': [0, 1, 2, 3, 4, 1]},
    'B6':      {'strings': [-1, 2, 4, 4, 4, 4], 'baseFret': 1, 'barres': [], 'fingers': [0, 1, 2, 3, 4, 4]},
    'Bm6':     {'strings': [-1, 2, 0, 1, 0, 2], 'baseFret': 1, 'barres': [], 'fingers': [0, 2, 0, 1, 0, 3]},
    'B9':      {'strings': [-1, 2, 1, 2, 2, 2], 'baseFret': 1, 'barres': [], 'fingers': [0, 2, 1, 3, 4, 4]},

    # === 延伸和弦補充 ===
    'Eb11':    {'strings': [-1, -1, 1, 1, 2, 1], 'baseFret': 1, 'barres': [1], 'fingers': [0, 0, 1, 1, 2, 1]},
    'Cb':      {'strings': [-1, 2, 4, 4, 4, 2], 'baseFret': 1, 'barres': [2], 'fingers': [0, 1, 2, 3, 4, 1]},  # = B
    'F11':     {'strings': [1, 1, 1, 2, 1, 1], 'baseFret': 1, 'barres': [1], 'fingers': [1, 1, 1, 2, 1, 1]},
}

# 烏克麗麗和弦 (4弦, GCEA)
UKULELE_CHORDS = {
    'C':       {'strings': [0, 0, 0, 3], 'baseFret': 1, 'barres': [], 'fingers': [0, 0, 0, 1]},
    'Cm':      {'strings': [0, 3, 3, 3], 'baseFret': 1, 'barres': [3], 'fingers': [0, 1, 1, 1]},
    'C7':      {'strings': [0, 0, 0, 1], 'baseFret': 1, 'barres': [], 'fingers': [0, 0, 0, 1]},
    'Cmaj7':   {'strings': [0, 0, 0, 2], 'baseFret': 1, 'barres': [], 'fingers': [0, 0, 0, 1]},
    'Cm7':     {'strings': [3, 3, 3, 3], 'baseFret': 1, 'barres': [3], 'fingers': [1, 1, 1, 1]},
    'C#':      {'strings': [1, 1, 1, 4], 'baseFret': 1, 'barres': [1], 'fingers': [1, 1, 1, 2]},
    'Db':      {'strings': [1, 1, 1, 4], 'baseFret': 1, 'barres': [1], 'fingers': [1, 1, 1, 2]},
    'C#m':     {'strings': [0, 4, 4, 4], 'baseFret': 1, 'barres': [4], 'fingers': [0, 1, 1, 1]},
    'Dbm':     {'strings': [0, 4, 4, 4], 'baseFret': 1, 'barres': [4], 'fingers': [0, 1, 1, 1]},
    'D':       {'strings': [2, 2, 2, 0], 'baseFret': 1, 'barres': [], 'fingers': [1, 2, 3, 0]},
    'Dm':      {'strings': [2, 2, 1, 0], 'baseFret': 1, 'barres': [], 'fingers': [2, 3, 1, 0]},
    'D7':      {'strings': [2, 2, 2, 3], 'baseFret': 1, 'barres': [], 'fingers': [1, 2, 3, 4]},
    'Dm7':     {'strings': [2, 2, 1, 3], 'baseFret': 1, 'barres': [], 'fingers': [2, 3, 1, 4]},
    'E':       {'strings': [1, 4, 0, 2], 'baseFret': 1, 'barres': [], 'fingers': [1, 3, 0, 2]},
    'Em':      {'strings': [0, 4, 3, 2], 'baseFret': 1, 'barres': [], 'fingers': [0, 3, 2, 1]},
    'E7':      {'strings': [1, 2, 0, 2], 'baseFret': 1, 'barres': [], 'fingers': [1, 2, 0, 3]},
    'Em7':     {'strings': [0, 2, 0, 2], 'baseFret': 1, 'barres': [], 'fingers': [0, 1, 0, 2]},
    'F':       {'strings': [2, 0, 1, 0], 'baseFret': 1, 'barres': [], 'fingers': [2, 0, 1, 0]},
    'Fm':      {'strings': [1, 0, 1, 3], 'baseFret': 1, 'barres': [], 'fingers': [1, 0, 2, 3]},
    'F7':      {'strings': [2, 3, 1, 0], 'baseFret': 1, 'barres': [], 'fingers': [2, 3, 1, 0]},
    'Fm7':     {'strings': [1, 3, 1, 3], 'baseFret': 1, 'barres': [], 'fingers': [1, 3, 2, 4]},
    'G':       {'strings': [0, 2, 3, 2], 'baseFret': 1, 'barres': [], 'fingers': [0, 1, 3, 2]},
    'Gm':      {'strings': [0, 2, 3, 1], 'baseFret': 1, 'barres': [], 'fingers': [0, 2, 3, 1]},
    'G7':      {'strings': [0, 2, 1, 2], 'baseFret': 1, 'barres': [], 'fingers': [0, 2, 1, 3]},
    'Gm7':     {'strings': [0, 2, 1, 1], 'baseFret': 1, 'barres': [], 'fingers': [0, 3, 1, 2]},
    'A':       {'strings': [2, 1, 0, 0], 'baseFret': 1, 'barres': [], 'fingers': [2, 1, 0, 0]},
    'Am':      {'strings': [2, 0, 0, 0], 'baseFret': 1, 'barres': [], 'fingers': [1, 0, 0, 0]},
    'A7':      {'strings': [0, 1, 0, 0], 'baseFret': 1, 'barres': [], 'fingers': [0, 1, 0, 0]},
    'Am7':     {'strings': [0, 0, 0, 0], 'baseFret': 1, 'barres': [0], 'fingers': [0, 0, 0, 0]},
    'A#':      {'strings': [3, 2, 1, 1], 'baseFret': 1, 'barres': [1], 'fingers': [3, 2, 1, 1]},
    'Bb':      {'strings': [3, 2, 1, 1], 'baseFret': 1, 'barres': [1], 'fingers': [3, 2, 1, 1]},
    'A#m':     {'strings': [3, 1, 1, 1], 'baseFret': 1, 'barres': [1], 'fingers': [2, 1, 1, 1]},
    'Bbm':     {'strings': [3, 1, 1, 1], 'baseFret': 1, 'barres': [1], 'fingers': [2, 1, 1, 1]},
    'Bb7':     {'strings': [1, 2, 1, 1], 'baseFret': 1, 'barres': [1], 'fingers': [1, 2, 1, 1]},
    'B':       {'strings': [4, 3, 2, 2], 'baseFret': 1, 'barres': [2], 'fingers': [3, 2, 1, 1]},
    'Bm':      {'strings': [4, 2, 2, 2], 'baseFret': 1, 'barres': [2], 'fingers': [2, 1, 1, 1]},
    'B7':      {'strings': [2, 3, 2, 2], 'baseFret': 1, 'barres': [2], 'fingers': [1, 2, 1, 1]},
    'D#':      {'strings': [0, 3, 3, 1], 'baseFret': 1, 'barres': [], 'fingers': [0, 2, 3, 1]},
    'Eb':      {'strings': [0, 3, 3, 1], 'baseFret': 1, 'barres': [], 'fingers': [0, 2, 3, 1]},
    'D#m':     {'strings': [3, 3, 2, 1], 'baseFret': 1, 'barres': [], 'fingers': [3, 4, 2, 1]},
    'Ebm':     {'strings': [3, 3, 2, 1], 'baseFret': 1, 'barres': [], 'fingers': [3, 4, 2, 1]},
    'G#':      {'strings': [5, 3, 4, 3], 'baseFret': 1, 'barres': [], 'fingers': [4, 1, 3, 2]},
    'Ab':      {'strings': [5, 3, 4, 3], 'baseFret': 1, 'barres': [], 'fingers': [4, 1, 3, 2]},
    'G#m':     {'strings': [4, 3, 4, 2], 'baseFret': 1, 'barres': [], 'fingers': [3, 2, 4, 1]},
    'Abm':     {'strings': [4, 3, 4, 2], 'baseFret': 1, 'barres': [], 'fingers': [3, 2, 4, 1]},
    'G#7':     {'strings': [1, 3, 2, 3], 'baseFret': 1, 'barres': [], 'fingers': [1, 3, 2, 4]},
    'G#m7':    {'strings': [4, 3, 4, 4], 'baseFret': 1, 'barres': [], 'fingers': [2, 1, 3, 4]},

    # === maj7 系列 ===
    'Dmaj7':   {'strings': [2, 2, 2, 4], 'baseFret': 1, 'barres': [], 'fingers': [1, 2, 3, 4]},
    'Emaj7':   {'strings': [1, 3, 0, 2], 'baseFret': 1, 'barres': [], 'fingers': [1, 3, 0, 2]},
    'Fmaj7':   {'strings': [2, 4, 1, 0], 'baseFret': 1, 'barres': [], 'fingers': [2, 3, 1, 0]},
    'Gmaj7':   {'strings': [0, 2, 2, 2], 'baseFret': 1, 'barres': [], 'fingers': [0, 1, 2, 3]},
    'Amaj7':   {'strings': [1, 1, 0, 0], 'baseFret': 1, 'barres': [], 'fingers': [1, 2, 0, 0]},
    'Bmaj7':   {'strings': [4, 3, 2, 1], 'baseFret': 1, 'barres': [], 'fingers': [4, 3, 2, 1]},
    'Bbmaj7':  {'strings': [3, 2, 1, 0], 'baseFret': 1, 'barres': [], 'fingers': [3, 2, 1, 0]},
    'Ebmaj7':  {'strings': [3, 3, 3, 1], 'baseFret': 1, 'barres': [3], 'fingers': [1, 1, 1, 2]},
    'Abmaj7':  {'strings': [1, 3, 3, 3], 'baseFret': 1, 'barres': [3], 'fingers': [2, 1, 1, 1]},
    'C#maj7':  {'strings': [1, 1, 0, 1], 'baseFret': 1, 'barres': [], 'fingers': [1, 2, 0, 3]},
    'Dbmaj7':  {'strings': [1, 1, 0, 1], 'baseFret': 1, 'barres': [], 'fingers': [1, 2, 0, 3]},
    'F#maj7':  {'strings': [3, 5, 2, 1], 'baseFret': 1, 'barres': [], 'fingers': [3, 4, 2, 1]},
    'Gbmaj7':  {'strings': [3, 5, 2, 1], 'baseFret': 1, 'barres': [], 'fingers': [3, 4, 2, 1]},

    # === dim 系列 ===
    'Cdim':    {'strings': [0, 3, 2, 3], 'baseFret': 1, 'barres': [], 'fingers': [0, 2, 1, 3]},
    'Ddim':    {'strings': [1, 2, 1, 2], 'baseFret': 1, 'barres': [], 'fingers': [1, 3, 2, 4]},
    'Edim':    {'strings': [0, 4, 0, 1], 'baseFret': 1, 'barres': [], 'fingers': [0, 2, 0, 1]},
    'Fdim':    {'strings': [1, 2, 1, 2], 'baseFret': 1, 'barres': [], 'fingers': [1, 3, 2, 4]},
    'Gdim':    {'strings': [0, 1, 0, 1], 'baseFret': 1, 'barres': [], 'fingers': [0, 1, 0, 2]},
    'Adim':    {'strings': [2, 3, 2, 3], 'baseFret': 1, 'barres': [], 'fingers': [1, 3, 2, 4]},
    'Bdim':    {'strings': [1, 2, 1, 2], 'baseFret': 4, 'barres': [], 'fingers': [1, 3, 2, 4]},
    'Bbdim':   {'strings': [0, 1, 0, 1], 'baseFret': 1, 'barres': [], 'fingers': [0, 1, 0, 2]},
    'Ebdim':   {'strings': [2, 3, 2, 3], 'baseFret': 1, 'barres': [], 'fingers': [1, 3, 2, 4]},
    'Abdim':   {'strings': [1, 2, 1, 2], 'baseFret': 1, 'barres': [], 'fingers': [1, 3, 2, 4]},

    # === aug 系列 ===
    'Caug':    {'strings': [1, 0, 0, 3], 'baseFret': 1, 'barres': [], 'fingers': [1, 0, 0, 2]},
    'Daug':    {'strings': [3, 2, 2, 1], 'baseFret': 1, 'barres': [], 'fingers': [4, 2, 3, 1]},
    'Eaug':    {'strings': [1, 0, 0, 3], 'baseFret': 1, 'barres': [], 'fingers': [1, 0, 0, 2]},
    'Faug':    {'strings': [2, 1, 1, 0], 'baseFret': 1, 'barres': [], 'fingers': [3, 1, 2, 0]},
    'Gaug':    {'strings': [0, 3, 3, 2], 'baseFret': 1, 'barres': [], 'fingers': [0, 2, 3, 1]},
    'Aaug':    {'strings': [2, 1, 1, 0], 'baseFret': 1, 'barres': [], 'fingers': [3, 1, 2, 0]},
    'Baug':    {'strings': [0, 3, 3, 2], 'baseFret': 1, 'barres': [], 'fingers': [0, 2, 3, 1]},

    # === sus 系列 ===
    'Csus2':   {'strings': [0, 2, 3, 3], 'baseFret': 1, 'barres': [], 'fingers': [0, 1, 2, 3]},
    'Csus4':   {'strings': [0, 0, 1, 3], 'baseFret': 1, 'barres': [], 'fingers': [0, 0, 1, 2]},
    'Dsus2':   {'strings': [2, 2, 0, 0], 'baseFret': 1, 'barres': [], 'fingers': [1, 2, 0, 0]},
    'Dsus4':   {'strings': [0, 2, 3, 0], 'baseFret': 1, 'barres': [], 'fingers': [0, 1, 2, 0]},
    'Esus4':   {'strings': [2, 4, 0, 2], 'baseFret': 1, 'barres': [], 'fingers': [1, 3, 0, 2]},
    'Fsus2':   {'strings': [0, 0, 1, 0], 'baseFret': 1, 'barres': [], 'fingers': [0, 0, 1, 0]},
    'Gsus2':   {'strings': [0, 2, 3, 0], 'baseFret': 1, 'barres': [], 'fingers': [0, 1, 2, 0]},
    'Gsus4':   {'strings': [0, 2, 3, 3], 'baseFret': 1, 'barres': [], 'fingers': [0, 1, 2, 3]},
    'Asus2':   {'strings': [2, 4, 0, 0], 'baseFret': 1, 'barres': [], 'fingers': [1, 2, 0, 0]},
    'Asus4':   {'strings': [2, 2, 0, 0], 'baseFret': 1, 'barres': [], 'fingers': [1, 2, 0, 0]},

    # === 6 和弦 ===
    'C6':      {'strings': [0, 0, 0, 0], 'baseFret': 1, 'barres': [], 'fingers': [0, 0, 0, 0]},
    'D6':      {'strings': [2, 2, 2, 2], 'baseFret': 1, 'barres': [2], 'fingers': [1, 1, 1, 1]},
    'E6':      {'strings': [4, 4, 4, 4], 'baseFret': 1, 'barres': [4], 'fingers': [1, 1, 1, 1]},
    'F6':      {'strings': [2, 2, 1, 3], 'baseFret': 1, 'barres': [], 'fingers': [2, 3, 1, 4]},
    'G6':      {'strings': [0, 2, 0, 2], 'baseFret': 1, 'barres': [], 'fingers': [0, 1, 0, 2]},
    'A6':      {'strings': [2, 4, 2, 4], 'baseFret': 1, 'barres': [], 'fingers': [1, 3, 2, 4]},

    # === 9 和弦 ===
    'C9':      {'strings': [0, 2, 0, 1], 'baseFret': 1, 'barres': [], 'fingers': [0, 2, 0, 1]},
    'D9':      {'strings': [2, 4, 2, 3], 'baseFret': 1, 'barres': [], 'fingers': [1, 4, 2, 3]},
    'G9':      {'strings': [2, 2, 1, 2], 'baseFret': 1, 'barres': [], 'fingers': [2, 3, 1, 4]},
    'A9':      {'strings': [0, 1, 0, 2], 'baseFret': 1, 'barres': [], 'fingers': [0, 1, 0, 2]},

    # === 更多 7 和弦（補齊升降號） ===
    'Eb7':     {'strings': [3, 3, 3, 4], 'baseFret': 1, 'barres': [3], 'fingers': [1, 1, 1, 2]},
    'Ab7':     {'strings': [1, 3, 2, 3], 'baseFret': 1, 'barres': [], 'fingers': [1, 3, 2, 4]},
    'Abm7':    {'strings': [4, 3, 4, 4], 'baseFret': 1, 'barres': [], 'fingers': [2, 1, 3, 4]},
    'C#7':     {'strings': [1, 1, 1, 2], 'baseFret': 1, 'barres': [1], 'fingers': [1, 1, 1, 2]},
    'Db7':     {'strings': [1, 1, 1, 2], 'baseFret': 1, 'barres': [1], 'fingers': [1, 1, 1, 2]},
    'C#m7':    {'strings': [1, 1, 0, 2], 'baseFret': 1, 'barres': [], 'fingers': [1, 2, 0, 3]},
    'Dbm7':    {'strings': [1, 1, 0, 2], 'baseFret': 1, 'barres': [], 'fingers': [1, 2, 0, 3]},
    'F#':      {'strings': [3, 1, 2, 1], 'baseFret': 1, 'barres': [1], 'fingers': [3, 1, 2, 1]},
    'F#m':     {'strings': [2, 1, 2, 0], 'baseFret': 1, 'barres': [], 'fingers': [2, 1, 3, 0]},
    'F#7':     {'strings': [3, 4, 2, 1], 'baseFret': 1, 'barres': [], 'fingers': [3, 4, 2, 1]},
    'F#m7':    {'strings': [2, 4, 2, 4], 'baseFret': 1, 'barres': [], 'fingers': [1, 3, 2, 4]},
    'Gb':      {'strings': [3, 1, 2, 1], 'baseFret': 1, 'barres': [1], 'fingers': [3, 1, 2, 1]},
    'Gbm':     {'strings': [2, 1, 2, 0], 'baseFret': 1, 'barres': [], 'fingers': [2, 1, 3, 0]},
    'Gbm7':    {'strings': [2, 4, 2, 4], 'baseFret': 1, 'barres': [], 'fingers': [1, 3, 2, 4]},
    'Bbm7':    {'strings': [1, 1, 1, 1], 'baseFret': 1, 'barres': [1], 'fingers': [1, 1, 1, 1]},
    'Bm7':     {'strings': [2, 2, 2, 2], 'baseFret': 1, 'barres': [2], 'fingers': [1, 1, 1, 1]},
    'Ebm7':    {'strings': [3, 3, 2, 4], 'baseFret': 1, 'barres': [], 'fingers': [2, 3, 1, 4]},
}


# 吉他和弦替代把位 (每個和弦 2~4 種按法)
GUITAR_VOICINGS = {
    'C': [
        {'label': '開放', 'strings': [-1, 3, 2, 0, 1, 0], 'baseFret': 1, 'barres': [], 'fingers': [0, 3, 2, 0, 1, 0]},
        {'label': '封閉 3格', 'strings': [-1, 3, 5, 5, 5, 3], 'baseFret': 1, 'barres': [3], 'fingers': [0, 1, 3, 3, 3, 1]},
        {'label': '封閉 8格', 'strings': [1, 1, 3, 3, 3, 1], 'baseFret': 8, 'barres': [1], 'fingers': [1, 1, 2, 3, 4, 1]},
    ],
    'Cm': [
        {'label': '封閉 3格', 'strings': [-1, 3, 5, 5, 4, 3], 'baseFret': 1, 'barres': [3], 'fingers': [0, 1, 3, 4, 2, 1]},
        {'label': '封閉 8格', 'strings': [1, 1, 3, 3, 1, 1], 'baseFret': 8, 'barres': [1], 'fingers': [1, 1, 3, 4, 1, 1]},
        {'label': 'High', 'strings': [-1, -1, 1, 3, 2, 1], 'baseFret': 8, 'barres': [1], 'fingers': [0, 0, 1, 4, 3, 1]},
    ],
    'C7': [
        {'label': '開放', 'strings': [-1, 3, 2, 3, 1, 0], 'baseFret': 1, 'barres': [], 'fingers': [0, 3, 2, 4, 1, 0]},
        {'label': '封閉 3格', 'strings': [-1, 3, 5, 3, 5, 3], 'baseFret': 1, 'barres': [3], 'fingers': [0, 1, 3, 1, 4, 1]},
        {'label': '封閉 8格', 'strings': [1, 1, 3, 1, 3, 1], 'baseFret': 8, 'barres': [1], 'fingers': [1, 1, 3, 1, 4, 1]},
    ],
    'Cmaj7': [
        {'label': '開放', 'strings': [-1, 3, 2, 0, 0, 0], 'baseFret': 1, 'barres': [], 'fingers': [0, 3, 2, 0, 0, 0]},
        {'label': '封閉 3格', 'strings': [-1, 3, 5, 4, 5, 3], 'baseFret': 1, 'barres': [3], 'fingers': [0, 1, 3, 2, 4, 1]},
    ],
    'D': [
        {'label': '開放', 'strings': [-1, -1, 0, 2, 3, 2], 'baseFret': 1, 'barres': [], 'fingers': [0, 0, 0, 1, 3, 2]},
        {'label': '封閉 5格', 'strings': [-1, 5, 7, 7, 7, 5], 'baseFret': 1, 'barres': [5], 'fingers': [0, 1, 3, 3, 3, 1]},
        {'label': '封閉 10格', 'strings': [1, 1, 3, 3, 3, 1], 'baseFret': 10, 'barres': [1], 'fingers': [1, 1, 2, 3, 4, 1]},
    ],
    'Dm': [
        {'label': '開放', 'strings': [-1, -1, 0, 2, 3, 1], 'baseFret': 1, 'barres': [], 'fingers': [0, 0, 0, 2, 3, 1]},
        {'label': '封閉 5格', 'strings': [-1, 5, 7, 7, 6, 5], 'baseFret': 1, 'barres': [5], 'fingers': [0, 1, 3, 4, 2, 1]},
        {'label': '封閉 10格', 'strings': [1, 1, 3, 3, 1, 1], 'baseFret': 10, 'barres': [1], 'fingers': [1, 1, 3, 4, 1, 1]},
    ],
    'D7': [
        {'label': '開放', 'strings': [-1, -1, 0, 2, 1, 2], 'baseFret': 1, 'barres': [], 'fingers': [0, 0, 0, 2, 1, 3]},
        {'label': '封閉 5格', 'strings': [-1, 5, 7, 5, 7, 5], 'baseFret': 1, 'barres': [5], 'fingers': [0, 1, 3, 1, 4, 1]},
    ],
    'Dmaj7': [
        {'label': '開放', 'strings': [-1, -1, 0, 2, 2, 2], 'baseFret': 1, 'barres': [], 'fingers': [0, 0, 0, 1, 2, 3]},
        {'label': '封閉 5格', 'strings': [-1, 5, 7, 6, 7, 5], 'baseFret': 1, 'barres': [5], 'fingers': [0, 1, 3, 2, 4, 1]},
    ],
    'E': [
        {'label': '開放', 'strings': [0, 2, 2, 1, 0, 0], 'baseFret': 1, 'barres': [], 'fingers': [0, 2, 3, 1, 0, 0]},
        {'label': '封閉 7格', 'strings': [-1, 7, 9, 9, 9, 7], 'baseFret': 1, 'barres': [7], 'fingers': [0, 1, 3, 3, 3, 1]},
    ],
    'Em': [
        {'label': '開放', 'strings': [0, 2, 2, 0, 0, 0], 'baseFret': 1, 'barres': [], 'fingers': [0, 2, 3, 0, 0, 0]},
        {'label': '封閉 7格', 'strings': [-1, 7, 9, 9, 8, 7], 'baseFret': 1, 'barres': [7], 'fingers': [0, 1, 3, 4, 2, 1]},
        {'label': 'High', 'strings': [-1, -1, 2, 0, 0, 0], 'baseFret': 7, 'barres': [], 'fingers': [0, 0, 2, 0, 0, 0]},
    ],
    'E7': [
        {'label': '開放', 'strings': [0, 2, 0, 1, 0, 0], 'baseFret': 1, 'barres': [], 'fingers': [0, 2, 0, 1, 0, 0]},
        {'label': '封閉 7格', 'strings': [-1, 7, 9, 7, 9, 7], 'baseFret': 1, 'barres': [7], 'fingers': [0, 1, 3, 1, 4, 1]},
    ],
    'Emaj7': [
        {'label': '開放', 'strings': [0, 2, 1, 1, 0, 0], 'baseFret': 1, 'barres': [], 'fingers': [0, 3, 1, 2, 0, 0]},
        {'label': '封閉 7格', 'strings': [-1, 7, 9, 8, 9, 7], 'baseFret': 1, 'barres': [7], 'fingers': [0, 1, 3, 2, 4, 1]},
    ],
    'F': [
        {'label': '封閉', 'strings': [1, 1, 2, 3, 3, 1], 'baseFret': 1, 'barres': [1], 'fingers': [1, 1, 2, 3, 4, 1]},
        {'label': 'Easy', 'strings': [-1, -1, 3, 2, 1, 1], 'baseFret': 1, 'barres': [1], 'fingers': [0, 0, 3, 2, 1, 1]},
        {'label': '封閉 8格', 'strings': [-1, 8, 10, 10, 10, 8], 'baseFret': 1, 'barres': [8], 'fingers': [0, 1, 3, 3, 3, 1]},
    ],
    'Fm': [
        {'label': '封閉', 'strings': [1, 1, 3, 3, 1, 1], 'baseFret': 1, 'barres': [1], 'fingers': [1, 1, 3, 4, 1, 1]},
        {'label': '封閉 8格', 'strings': [-1, 8, 10, 10, 9, 8], 'baseFret': 1, 'barres': [8], 'fingers': [0, 1, 3, 4, 2, 1]},
    ],
    'F7': [
        {'label': '封閉', 'strings': [1, 1, 2, 1, 3, 1], 'baseFret': 1, 'barres': [1], 'fingers': [1, 1, 2, 1, 3, 1]},
        {'label': 'Easy', 'strings': [-1, -1, 3, 2, 1, 0], 'baseFret': 1, 'barres': [], 'fingers': [0, 0, 4, 3, 2, 0]},
    ],
    'Fmaj7': [
        {'label': '開放', 'strings': [-1, -1, 3, 2, 1, 0], 'baseFret': 1, 'barres': [], 'fingers': [0, 0, 3, 2, 1, 0]},
        {'label': '封閉', 'strings': [1, 1, 2, 2, 3, 1], 'baseFret': 1, 'barres': [1], 'fingers': [1, 1, 2, 3, 4, 1]},
    ],
    'G': [
        {'label': '開放', 'strings': [3, 2, 0, 0, 0, 3], 'baseFret': 1, 'barres': [], 'fingers': [2, 1, 0, 0, 0, 3]},
        {'label': '封閉 3格', 'strings': [3, 3, 5, 5, 5, 3], 'baseFret': 1, 'barres': [3], 'fingers': [1, 1, 2, 3, 4, 1]},
        {'label': 'High', 'strings': [-1, -1, 5, 4, 3, 3], 'baseFret': 1, 'barres': [3], 'fingers': [0, 0, 4, 3, 1, 1]},
    ],
    'Gm': [
        {'label': '封閉 3格', 'strings': [3, 5, 5, 3, 3, 3], 'baseFret': 1, 'barres': [3], 'fingers': [1, 3, 4, 1, 1, 1]},
        {'label': '封閉 10格', 'strings': [-1, 10, 12, 12, 11, 10], 'baseFret': 1, 'barres': [10], 'fingers': [0, 1, 3, 4, 2, 1]},
        {'label': 'Easy', 'strings': [-1, -1, 5, 3, 3, 3], 'baseFret': 1, 'barres': [3], 'fingers': [0, 0, 4, 1, 1, 1]},
    ],
    'G7': [
        {'label': '開放', 'strings': [3, 2, 0, 0, 0, 1], 'baseFret': 1, 'barres': [], 'fingers': [3, 2, 0, 0, 0, 1]},
        {'label': '封閉 3格', 'strings': [3, 3, 5, 3, 5, 3], 'baseFret': 1, 'barres': [3], 'fingers': [1, 1, 3, 1, 4, 1]},
    ],
    'Gmaj7': [
        {'label': '開放', 'strings': [3, 2, 0, 0, 0, 2], 'baseFret': 1, 'barres': [], 'fingers': [3, 2, 0, 0, 0, 1]},
        {'label': '封閉 3格', 'strings': [3, 3, 5, 4, 5, 3], 'baseFret': 1, 'barres': [3], 'fingers': [1, 1, 3, 2, 4, 1]},
    ],
    'A': [
        {'label': '開放', 'strings': [-1, 0, 2, 2, 2, 0], 'baseFret': 1, 'barres': [], 'fingers': [0, 0, 1, 2, 3, 0]},
        {'label': '封閉 5格', 'strings': [5, 5, 7, 7, 7, 5], 'baseFret': 1, 'barres': [5], 'fingers': [1, 1, 2, 3, 4, 1]},
        {'label': 'High', 'strings': [-1, -1, 7, 6, 5, 5], 'baseFret': 1, 'barres': [5], 'fingers': [0, 0, 4, 3, 1, 1]},
    ],
    'Am': [
        {'label': '開放', 'strings': [-1, 0, 2, 2, 1, 0], 'baseFret': 1, 'barres': [], 'fingers': [0, 0, 2, 3, 1, 0]},
        {'label': '封閉 5格', 'strings': [5, 5, 7, 7, 5, 5], 'baseFret': 1, 'barres': [5], 'fingers': [1, 1, 3, 4, 1, 1]},
        {'label': 'High', 'strings': [-1, -1, 7, 5, 5, 5], 'baseFret': 1, 'barres': [5], 'fingers': [0, 0, 4, 1, 1, 1]},
    ],
    'A7': [
        {'label': '開放', 'strings': [-1, 0, 2, 0, 2, 0], 'baseFret': 1, 'barres': [], 'fingers': [0, 0, 1, 0, 2, 0]},
        {'label': '封閉 5格', 'strings': [5, 5, 7, 5, 7, 5], 'baseFret': 1, 'barres': [5], 'fingers': [1, 1, 3, 1, 4, 1]},
    ],
    'Amaj7': [
        {'label': '開放', 'strings': [-1, 0, 2, 1, 2, 0], 'baseFret': 1, 'barres': [], 'fingers': [0, 0, 2, 1, 3, 0]},
        {'label': '封閉 5格', 'strings': [5, 5, 7, 6, 7, 5], 'baseFret': 1, 'barres': [5], 'fingers': [1, 1, 3, 2, 4, 1]},
    ],
    'B': [
        {'label': '封閉 2格', 'strings': [-1, 2, 4, 4, 4, 2], 'baseFret': 1, 'barres': [2], 'fingers': [0, 1, 2, 3, 4, 1]},
        {'label': '封閉 7格', 'strings': [7, 7, 9, 9, 9, 7], 'baseFret': 1, 'barres': [7], 'fingers': [1, 1, 2, 3, 4, 1]},
        {'label': 'Easy', 'strings': [-1, 2, 4, 4, 4, -1], 'baseFret': 1, 'barres': [], 'fingers': [0, 1, 2, 3, 4, 0]},
    ],
    'Bm': [
        {'label': '封閉 2格', 'strings': [-1, 2, 4, 4, 3, 2], 'baseFret': 1, 'barres': [2], 'fingers': [0, 1, 3, 4, 2, 1]},
        {'label': '封閉 7格', 'strings': [7, 7, 9, 9, 7, 7], 'baseFret': 1, 'barres': [7], 'fingers': [1, 1, 3, 4, 1, 1]},
        {'label': 'Easy', 'strings': [-1, -1, 4, 4, 3, 2], 'baseFret': 1, 'barres': [], 'fingers': [0, 0, 3, 4, 2, 1]},
    ],
    'B7': [
        {'label': '開放', 'strings': [-1, 2, 1, 2, 0, 2], 'baseFret': 1, 'barres': [], 'fingers': [0, 2, 1, 3, 0, 4]},
        {'label': '封閉 7格', 'strings': [7, 7, 9, 7, 9, 7], 'baseFret': 1, 'barres': [7], 'fingers': [1, 1, 3, 1, 4, 1]},
    ],
    'Bmaj7': [
        {'label': '封閉 2格', 'strings': [-1, 2, 4, 3, 4, 2], 'baseFret': 1, 'barres': [2], 'fingers': [0, 1, 3, 2, 4, 1]},
        {'label': '封閉 7格', 'strings': [7, 7, 9, 8, 9, 7], 'baseFret': 1, 'barres': [7], 'fingers': [1, 1, 3, 2, 4, 1]},
    ],
    'Bb': [
        {'label': '封閉 1格', 'strings': [-1, 1, 3, 3, 3, 1], 'baseFret': 1, 'barres': [1], 'fingers': [0, 1, 2, 3, 4, 1]},
        {'label': '封閉 6格', 'strings': [6, 6, 8, 8, 8, 6], 'baseFret': 1, 'barres': [6], 'fingers': [1, 1, 2, 3, 4, 1]},
        {'label': 'Easy', 'strings': [-1, 1, 3, 3, 3, -1], 'baseFret': 1, 'barres': [], 'fingers': [0, 1, 2, 3, 4, 0]},
    ],
    'Bbm': [
        {'label': '封閉 1格', 'strings': [-1, 1, 3, 3, 2, 1], 'baseFret': 1, 'barres': [1], 'fingers': [0, 1, 3, 4, 2, 1]},
        {'label': '封閉 6格', 'strings': [6, 6, 8, 8, 6, 6], 'baseFret': 1, 'barres': [6], 'fingers': [1, 1, 3, 4, 1, 1]},
    ],
}

# ---------------------------------------------------------------------------
# Accordion: 21-button Stradella bass map (3 rows × 7 columns)
# Columns follow circle of fifths: Bb, F, C, G, D, A, E
# Rows: 0=Bass (single note), 1=Major chord, 2=Minor chord
# C bass (col=2) has a concave dimple for tactile reference.
# ---------------------------------------------------------------------------
_ACC_COLS = ["Bb", "F", "C", "G", "D", "A", "E"]
_ACC_COL_IDX = {n: i for i, n in enumerate(_ACC_COLS)}
# Enharmonic mapping to bring roots into the 7-column range
_ACC_ENHARMONIC = {
    "A#": "Bb", "Cb": "B", "B#": "C", "Fb": "E", "E#": "F",
    "Gb": "F#", "G#": "Ab", "D#": "Eb", "C#": "Db",
}

def _acc_resolve(chord_name):
    """Resolve a chord name to accordion bass buttons.
    Returns dict with keys: buttons, altBass, root, quality, warning (optional).
    """
    import re as _re
    m = _re.match(r'^([A-G][b#]?)(.*)', chord_name)
    if not m:
        return None
    root, suffix = m.group(1), m.group(2)

    # Determine quality -> row
    is_minor = bool(_re.match(r'^m($|[^a])', suffix))  # m, m7, m6 but not maj
    row = 2 if is_minor else 1  # 1=Major, 2=Minor

    # Normalize root to one of the 7 columns
    norm_root = _ACC_ENHARMONIC.get(root, root)
    if norm_root not in _ACC_COL_IDX:
        # Root not available on 21-button layout (B, Eb, Ab, Db, F#)
        # Fallback: use the fifth as bass (e.g., B -> use E column)
        _fifth_map = {"B": "E", "Eb": "Bb", "Ab": "Bb", "Db": "F", "F#": "D"}
        fallback = _fifth_map.get(norm_root)
        if fallback and fallback in _ACC_COL_IDX:
            col = _ACC_COL_IDX[fallback]
            return {
                "buttons": [{"col": col, "row": 0, "label": fallback}],
                "altBass": None,
                "root": root,
                "quality": "minor" if is_minor else "major",
                "warning_key": "player.gt.acc_warn_no_bass_fifth",
                "warning_vars": {"root": root, "fallback": fallback},
                "available": False,
            }
        return {
            "buttons": [],
            "root": root,
            "quality": "minor" if is_minor else "major",
            "warning_key": "player.gt.acc_warn_unplayable",
            "warning_vars": {"chord": chord_name},
            "available": False,
        }

    col = _ACC_COL_IDX[norm_root]
    # Alt bass = one fifth up (next column to the right in circle-of-fifths)
    alt_col = col + 1 if col < 6 else None
    alt_bass = {"col": alt_col, "row": 0, "label": _ACC_COLS[alt_col]} if alt_col is not None else None

    return {
        "buttons": [
            {"col": col, "row": 0, "label": norm_root},       # Bass note
            {"col": col, "row": row, "label": chord_name},     # Chord button
        ],
        "altBass": alt_bass,
        "root": root,
        "quality": "minor" if is_minor else "major",
        "available": True,
    }


# Build static ACCORDION_BASS_MAP for common chords
ACCORDION_BASS_MAP = {}
for _root in _ACC_COLS:
    for _suf, _label in [("", _root), ("m", _root + "m")]:
        _name = _root + _suf
        _res = _acc_resolve(_name)
        if _res:
            ACCORDION_BASS_MAP[_name] = _res


# ---------------------------------------------------------------------------
# Arranger keyboard: Fingered chord input (Yamaha PSR-SX900 style)
# Left hand presses 3-4 keys below the split point to input a chord.
# The lowest note = root; interval pattern determines chord quality.
# ---------------------------------------------------------------------------
_ARR_FINGERED_INTERVALS = {
    # quality -> list of semitone offsets from root
    "":        [0, 4, 7],           # Major
    "m":       [0, 3, 7],           # Minor
    "min":     [0, 3, 7],
    "7":       [0, 4, 7, 10],       # Dominant 7th
    "maj7":    [0, 4, 7, 11],       # Major 7th
    "m7":      [0, 3, 7, 10],       # Minor 7th
    "min7":    [0, 3, 7, 10],
    "dim":     [0, 3, 6],           # Diminished
    "dim7":    [0, 3, 6, 9],        # Diminished 7th
    "aug":     [0, 4, 8],           # Augmented
    "sus4":    [0, 5, 7],           # Suspended 4th
    "sus2":    [0, 2, 7],           # Suspended 2nd
    "sus":     [0, 5, 7],
    "6":       [0, 4, 7, 9],        # Major 6th
    "m6":      [0, 3, 7, 9],        # Minor 6th
    "add9":    [0, 2, 4, 7],        # Add 9th
    "madd9":   [0, 2, 3, 7],        # Minor add 9th
    "m7b5":    [0, 3, 6, 10],       # Half-diminished
    "mM7":     [0, 3, 7, 11],       # Minor major 7th
    "aug7":    [0, 4, 8, 10],       # Augmented 7th
    "7sus4":   [0, 5, 7, 10],       # 7th sus4
    "7sus2":   [0, 2, 7, 10],       # 7th sus2
    # Extended chords: simplified to fit below split point
    "9":       [0, 4, 7, 10],       # Dom 9th → omit 9th (same as 7)
    "maj9":    [0, 4, 7, 11],       # Maj 9th → omit 9th (same as maj7)
    "m9":      [0, 3, 7, 10],       # Min 9th → omit 9th (same as m7)
    "11":      [0, 4, 7, 10],       # Dom 11th → simplified
    "m11":     [0, 3, 7, 10],
    "13":      [0, 4, 7, 10],
    "7b9":     [0, 4, 7, 10],       # 7b9 → same as 7 (narrow range)
    "7#9":     [0, 4, 7, 10],
    "7#11":    [0, 4, 7, 10],
    "7b5":     [0, 4, 6, 10],       # 7b5
    "7#5":     [0, 4, 8, 10],       # 7#5
    "mb5":     [0, 3, 6],           # = dim
    "6/9":     [0, 4, 7, 9],        # 6/9 → same as 6
}

# LH fingering for block chords (low→high): pinky=5 ... thumb=1
_ARR_FINGERING = {
    3: [5, 3, 1],
    4: [5, 3, 2, 1],
    5: [5, 4, 3, 2, 1],
}


def _arranger_resolve(chord_name, split_point=56):
    """Resolve chord to Fingered-mode MIDI notes below the split point.

    Returns dict: midi_notes, root, quality, fingering, split_point,
                  available, warning.
    """
    import re as _re
    m = _re.match(r'^([A-G][b#]?)(.*)', chord_name)
    if not m:
        return None
    root, suffix = m.group(1), m.group(2)

    # Handle slash chords: use the main chord
    if '/' in suffix:
        suffix = suffix.split('/')[0]

    # Look up interval template
    intervals = _ARR_FINGERED_INTERVALS.get(suffix)
    if intervals is None:
        # Try simplification: strip trailing numbers, etc.
        for try_q in [_re.sub(r'\d+$', '', suffix), _re.sub(r'(m?).*', r'\1', suffix), '']:
            if try_q in _ARR_FINGERED_INTERVALS:
                intervals = _ARR_FINGERED_INTERVALS[try_q]
                break
    if intervals is None:
        intervals = _ARR_FINGERED_INTERVALS[""]  # fallback to major

    # Compute root MIDI: place root in the lowest octave that fits in range
    from chord_table import root_to_semitone
    pc = root_to_semitone(root)  # 0-11, C=0
    midi_low = 36  # C1 = lowest key on 61-key keyboard

    # Find root octave: start from MIDI 36 upward
    root_midi = midi_low + pc
    if root_midi < midi_low:
        root_midi += 12

    # Build MIDI notes
    midi_notes = [root_midi + iv for iv in intervals]

    # Check if all notes fit at or below split point (split key belongs to LH)
    warning = None
    if any(n > split_point for n in midi_notes):
        # Try lowering root by an octave
        if root_midi - 12 >= midi_low:
            root_midi -= 12
            midi_notes = [root_midi + iv for iv in intervals]
        # If still exceeding, truncate to notes at or below split
        if any(n > split_point for n in midi_notes):
            original_count = len(midi_notes)
            midi_notes = [n for n in midi_notes if n <= split_point]
            if len(midi_notes) < 3:
                warning = f"分割點 {split_point} 太低，{chord_name} 按鍵不足 3 鍵"
            elif len(midi_notes) < original_count:
                warning = f"{chord_name} 部分音超出分割點，已省略"

    # Ensure minimum 3 notes
    available = len(midi_notes) >= 3

    # Assign fingering
    n = len(midi_notes)
    fingering = _ARR_FINGERING.get(n, list(range(5, 5 - n, -1)))

    return {
        "midi_notes": midi_notes,
        "root": root,
        "quality": suffix or "major",
        "fingering": fingering,
        "split_point": split_point,
        "available": available,
        "warning": warning,
    }


# ---- 樂器 → 和弦資料映射 (新增樂器在此加一行) ----
CHORD_DBS = {
    "guitar": GUITAR_CHORDS,
    "ukulele": UKULELE_CHORDS,
    "accordion": ACCORDION_BASS_MAP,
}

VOICING_DBS = {
    "guitar": GUITAR_VOICINGS,
}


def get_chord_diagram(chord_name, instrument='guitar'):
    """取得和弦圖資料"""
    import re as _re
    # Strip inversion suffix (e.g. "C#:1" → "C#")
    chord_name = _re.sub(r':\d+$', '', chord_name)

    # Accordion uses dynamic resolution, not static fretboard lookup
    if instrument == "accordion":
        result = _acc_resolve(chord_name)
        if result:
            result["name"] = chord_name
        return result

    # Arranger: Fingered chord input below split point
    if instrument == "arranger":
        result = _arranger_resolve(chord_name)
        if result:
            result["name"] = chord_name
        return result

    db = CHORD_DBS.get(instrument)
    if db is None:
        return None
    inst_meta = get_instrument(instrument)
    num_strings = inst_meta.get("num_strings", 6) if inst_meta else 6

    # 直接查找
    if chord_name in db:
        data = db[chord_name].copy()
        data['name'] = chord_name
        data['numStrings'] = num_strings
        return data

    # 嘗試等音和弦 (如 A# -> Bb)
    enharmonic = {
        'C#': 'Db', 'Db': 'C#',
        'D#': 'Eb', 'Eb': 'D#',
        'F#': 'Gb', 'Gb': 'F#',
        'G#': 'Ab', 'Ab': 'G#',
        'A#': 'Bb', 'Bb': 'A#',
        'Cb': 'B', 'B': 'Cb',
        'Fb': 'E', 'E': 'Fb',
    }

    import re

    # 斜線和弦 (如 G/D) → fallback 到主和弦 (G)
    if '/' in chord_name:
        main_chord = chord_name.split('/')[0]
        result = get_chord_diagram(main_chord, instrument)
        if result:
            result['name'] = chord_name
        return result

    m = re.match(r'^([A-G][b#]?)(.*)', chord_name)
    if m:
        root, suffix = m.group(1), m.group(2)
        if root in enharmonic:
            alt_name = enharmonic[root] + suffix
            if alt_name in db:
                data = db[alt_name].copy()
                data['name'] = chord_name
                data['numStrings'] = num_strings
                return data

        # Fallback: simplify chord quality (e.g. Dm6 -> Dm7 -> Dm, Cmaj9 -> Cmaj7 -> C)
        _simplify = [
            (r'm7b5', 'm7'),                                 # Gbm7b5 -> Gbm7 (half-dim: nearest shape)
            (r'(m?)(?:add|sus|aug|dim)?\d+', r'\g<1>7'),   # Dm6/Dm9/Dm11 -> Dm7
            (r'(m?)7', r'\g<1>'),                            # Dm7 -> Dm
            (r'maj7', ''),                                   # Cmaj7 -> C
            (r'dim7?', 'm'),                                 # Cdim -> Cm
            (r'aug', ''),                                    # Caug -> C
            (r'sus[24]', ''),                                # Csus4 -> C
        ]
        for pat, repl in _simplify:
            simpler = root + re.sub(pat, repl, suffix)
            if simpler != chord_name and simpler in db:
                data = db[simpler].copy()
                data['name'] = chord_name
                data['numStrings'] = num_strings
                return data
            # Also try enharmonic of simplified
            if root in enharmonic:
                alt_simpler = enharmonic[root] + re.sub(pat, repl, suffix)
                if alt_simpler != chord_name and alt_simpler in db:
                    data = db[alt_simpler].copy()
                    data['name'] = chord_name
                    data['numStrings'] = num_strings
                    return data

    return None


def get_chord_voicings(chord_name, instrument='guitar'):
    """取得和弦所有把位指法 (list of dicts)"""
    import re as _re
    chord_name = _re.sub(r':\d+$', '', chord_name)

    # Accordion has no voicing variants — return the single bass mapping
    if instrument == "accordion":
        result = _acc_resolve(chord_name)
        if result:
            result["name"] = chord_name
            return [result]
        return []

    # Arranger has no voicing variants — single Fingered resolution
    if instrument == "arranger":
        result = _arranger_resolve(chord_name)
        if result:
            result["name"] = chord_name
            return [result]
        return []

    inst_meta = get_instrument(instrument)
    num_strings = inst_meta.get("num_strings", 6) if inst_meta else 6

    voicings = []

    # Check instrument-specific voicings DB
    voicing_db = VOICING_DBS.get(instrument)
    if voicing_db and chord_name in voicing_db:
        for v in voicing_db[chord_name]:
            d = v.copy()
            d['name'] = chord_name
            d['numStrings'] = num_strings
            voicings.append(d)

    # Always include the default voicing from main dict
    default = get_chord_diagram(chord_name, instrument)
    if default:
        # Avoid duplicates: check if default is already in voicings
        default_strings = tuple(default.get('strings', []))
        already = any(tuple(v.get('strings', [])) == default_strings for v in voicings)
        if not already:
            default['label'] = default.get('label', '標準')
            voicings.insert(0, default)

    return voicings


def list_available_chords(instrument='guitar'):
    """列出可用的和弦"""
    db = CHORD_DBS.get(instrument, {})
    return sorted(db.keys())
