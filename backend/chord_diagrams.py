"""
吉他/烏克麗麗和弦圖資料庫
每個和弦包含: strings (按法), baseFret (起始格), barres (封弦格數列表)
strings: 從低音弦到高音弦, -1=悶音(X), 0=空弦(O), 1~=品格位置
"""

# 吉他和弦 (6弦, EADGBE)
GUITAR_CHORDS = {
    # === C 系列 ===
    'C':       {'strings': [-1, 3, 2, 0, 1, 0], 'baseFret': 1, 'barres': []},
    'Cm':      {'strings': [-1, 3, 5, 5, 4, 3], 'baseFret': 1, 'barres': [3]},
    'C7':      {'strings': [-1, 3, 2, 3, 1, 0], 'baseFret': 1, 'barres': []},
    'Cmaj7':   {'strings': [-1, 3, 2, 0, 0, 0], 'baseFret': 1, 'barres': []},
    'Cm7':     {'strings': [-1, 1, 3, 1, 2, 1], 'baseFret': 3, 'barres': [1]},
    'Cdim':    {'strings': [-1, 3, 4, 5, 4, -1], 'baseFret': 1, 'barres': []},
    'Cdim7':   {'strings': [-1, 3, 4, 2, 4, 2], 'baseFret': 1, 'barres': []},
    'Caug':    {'strings': [-1, 3, 2, 1, 1, 0], 'baseFret': 1, 'barres': []},
    'Csus2':   {'strings': [-1, 3, 3, 0, 1, 3], 'baseFret': 1, 'barres': []},
    'Csus4':   {'strings': [-1, 3, 3, 0, 1, 1], 'baseFret': 1, 'barres': []},
    'C6':      {'strings': [-1, 0, 2, 2, 1, 3], 'baseFret': 1, 'barres': []},
    'Cm6':     {'strings': [-1, -1, 1, 2, 1, 3], 'baseFret': 1, 'barres': []},
    'C9':      {'strings': [-1, 3, 2, 3, 3, 0], 'baseFret': 1, 'barres': []},
    'C6/9':    {'strings': [-1, 3, 2, 2, 3, 0], 'baseFret': 1, 'barres': []},
    'Cadd9':   {'strings': [-1, 3, 2, 0, 3, 0], 'baseFret': 1, 'barres': []},

    # === C#/Db 系列 ===
    'C#':      {'strings': [-1, 4, 3, 1, 2, 1], 'baseFret': 1, 'barres': [1]},
    'Db':      {'strings': [-1, 4, 3, 1, 2, 1], 'baseFret': 1, 'barres': [1]},
    'C#m':     {'strings': [-1, 4, 6, 6, 5, 4], 'baseFret': 1, 'barres': [4]},
    'Dbm':     {'strings': [-1, 4, 6, 6, 5, 4], 'baseFret': 1, 'barres': [4]},
    'C#7':     {'strings': [-1, 4, 3, 4, 2, -1], 'baseFret': 1, 'barres': []},
    'Db7':     {'strings': [-1, 4, 3, 4, 2, -1], 'baseFret': 1, 'barres': []},
    'C#maj7':  {'strings': [-1, 4, 3, 1, 1, 1], 'baseFret': 1, 'barres': [1]},
    'Dbmaj7':  {'strings': [-1, 4, 3, 1, 1, 1], 'baseFret': 1, 'barres': [1]},
    'C#m7':    {'strings': [-1, 4, 2, 4, 2, 4], 'baseFret': 1, 'barres': []},
    'Dbm7':    {'strings': [-1, 4, 2, 4, 2, 4], 'baseFret': 1, 'barres': []},
    'C#dim':   {'strings': [-1, 4, 5, 6, 5, -1], 'baseFret': 1, 'barres': []},
    'C#dim7':  {'strings': [-1, 4, 5, 3, 5, 3], 'baseFret': 1, 'barres': []},
    'C#aug':   {'strings': [-1, 4, 3, 2, 2, 1], 'baseFret': 1, 'barres': []},
    'C#sus4':  {'strings': [-1, 4, 4, 1, 2, 2], 'baseFret': 1, 'barres': []},

    # === D 系列 ===
    'D':       {'strings': [-1, -1, 0, 2, 3, 2], 'baseFret': 1, 'barres': []},
    'Dm':      {'strings': [-1, -1, 0, 2, 3, 1], 'baseFret': 1, 'barres': []},
    'D7':      {'strings': [-1, -1, 0, 2, 1, 2], 'baseFret': 1, 'barres': []},
    'Dmaj7':   {'strings': [-1, -1, 0, 2, 2, 2], 'baseFret': 1, 'barres': []},
    'Dm7':     {'strings': [-1, -1, 0, 2, 1, 1], 'baseFret': 1, 'barres': []},
    'Ddim':    {'strings': [-1, -1, 0, 1, 3, 1], 'baseFret': 1, 'barres': []},
    'Ddim7':   {'strings': [-1, -1, 0, 1, 0, 1], 'baseFret': 1, 'barres': []},
    'Daug':    {'strings': [-1, -1, 0, 3, 3, 2], 'baseFret': 1, 'barres': []},
    'Dsus2':   {'strings': [-1, -1, 0, 2, 3, 0], 'baseFret': 1, 'barres': []},
    'Dsus4':   {'strings': [-1, -1, 0, 2, 3, 3], 'baseFret': 1, 'barres': []},
    'D6':      {'strings': [-1, -1, 0, 2, 0, 2], 'baseFret': 1, 'barres': []},
    'Dm6':     {'strings': [-1, -1, 0, 2, 0, 1], 'baseFret': 1, 'barres': []},
    'D9':      {'strings': [-1, -1, 0, 2, 1, 0], 'baseFret': 1, 'barres': []},
    'D6/9':    {'strings': [2, 0, 0, 2, 0, 0], 'baseFret': 1, 'barres': []},
    'Dadd9':   {'strings': [-1, -1, 0, 2, 3, 0], 'baseFret': 1, 'barres': []},

    # === D#/Eb 系列 ===
    'Eb':      {'strings': [-1, -1, 1, 3, 4, 3], 'baseFret': 1, 'barres': []},
    'D#':      {'strings': [-1, -1, 1, 3, 4, 3], 'baseFret': 1, 'barres': []},
    'Ebm':     {'strings': [-1, -1, 1, 3, 4, 2], 'baseFret': 1, 'barres': []},
    'D#m':     {'strings': [-1, -1, 1, 3, 4, 2], 'baseFret': 1, 'barres': []},
    'Eb7':     {'strings': [-1, -1, 1, 3, 2, 3], 'baseFret': 1, 'barres': []},
    'Ebmaj7':  {'strings': [-1, -1, 1, 3, 3, 3], 'baseFret': 1, 'barres': []},
    'Ebm7':    {'strings': [-1, -1, 1, 3, 2, 2], 'baseFret': 1, 'barres': []},
    'Ebdim':   {'strings': [-1, -1, 1, 2, 4, 2], 'baseFret': 1, 'barres': []},
    'Ebdim7':  {'strings': [-1, -1, 1, 2, 1, 2], 'baseFret': 1, 'barres': []},
    'Ebaug':   {'strings': [-1, -1, 1, 0, 0, 3], 'baseFret': 1, 'barres': []},
    'Ebsus4':  {'strings': [-1, -1, 1, 3, 4, 4], 'baseFret': 1, 'barres': []},

    # === E 系列 ===
    'E':       {'strings': [0, 2, 2, 1, 0, 0], 'baseFret': 1, 'barres': []},
    'Em':      {'strings': [0, 2, 2, 0, 0, 0], 'baseFret': 1, 'barres': []},
    'E7':      {'strings': [0, 2, 0, 1, 0, 0], 'baseFret': 1, 'barres': []},
    'Emaj7':   {'strings': [0, 2, 1, 1, 0, 0], 'baseFret': 1, 'barres': []},
    'Em7':     {'strings': [0, 2, 0, 0, 0, 0], 'baseFret': 1, 'barres': []},
    'Edim':    {'strings': [0, 1, 2, 0, -1, -1], 'baseFret': 1, 'barres': []},
    'Edim7':   {'strings': [-1, 1, 2, 0, 2, 0], 'baseFret': 1, 'barres': []},
    'Eaug':    {'strings': [0, 3, 2, 1, 1, 0], 'baseFret': 1, 'barres': []},
    'Esus2':   {'strings': [0, 2, 4, 4, 0, 0], 'baseFret': 1, 'barres': []},
    'Esus4':   {'strings': [0, 2, 2, 2, 0, 0], 'baseFret': 1, 'barres': []},
    'E6':      {'strings': [0, 2, 2, 1, 2, 0], 'baseFret': 1, 'barres': []},
    'Em6':     {'strings': [0, 2, 2, 0, 2, 0], 'baseFret': 1, 'barres': []},
    'E9':      {'strings': [0, 2, 0, 1, 0, 2], 'baseFret': 1, 'barres': []},
    'Eadd9':   {'strings': [0, 2, 2, 1, 0, 2], 'baseFret': 1, 'barres': []},

    # === F 系列 ===
    'F':       {'strings': [1, 1, 2, 3, 3, 1], 'baseFret': 1, 'barres': [1]},
    'Fm':      {'strings': [1, 1, 3, 3, 1, 1], 'baseFret': 1, 'barres': [1]},
    'F7':      {'strings': [1, 1, 2, 1, 3, 1], 'baseFret': 1, 'barres': [1]},
    'Fmaj7':   {'strings': [-1, -1, 3, 2, 1, 0], 'baseFret': 1, 'barres': []},
    'Fm7':     {'strings': [1, 1, 1, 1, 1, 1], 'baseFret': 1, 'barres': [1]},
    'Fdim':    {'strings': [-1, -1, 3, 1, 0, 1], 'baseFret': 1, 'barres': []},
    'Fdim7':   {'strings': [-1, 2, 3, 1, 3, 1], 'baseFret': 1, 'barres': []},
    'Faug':    {'strings': [-1, -1, 3, 2, 2, 1], 'baseFret': 1, 'barres': []},
    'Fsus2':   {'strings': [-1, -1, 3, 0, 1, 1], 'baseFret': 1, 'barres': []},
    'Fsus4':   {'strings': [1, 1, 3, 3, 1, 1], 'baseFret': 1, 'barres': [1]},
    'F6':      {'strings': [1, -1, 0, 2, 1, 1], 'baseFret': 1, 'barres': []},
    'Fm6':     {'strings': [-1, -1, 0, 1, 1, 1], 'baseFret': 1, 'barres': []},
    'F9':      {'strings': [1, 0, 1, 0, 1, -1], 'baseFret': 1, 'barres': []},
    'Fadd9':   {'strings': [-1, -1, 3, 2, 1, 3], 'baseFret': 1, 'barres': []},

    # === F#/Gb 系列 ===
    'F#':      {'strings': [2, 4, 4, 3, 2, 2], 'baseFret': 1, 'barres': [2]},
    'Gb':      {'strings': [2, 4, 4, 3, 2, 2], 'baseFret': 1, 'barres': [2]},
    'F#m':     {'strings': [2, 4, 4, 2, 2, 2], 'baseFret': 1, 'barres': [2]},
    'Gbm':     {'strings': [2, 4, 4, 2, 2, 2], 'baseFret': 1, 'barres': [2]},
    'F#7':     {'strings': [2, 4, 2, 3, 2, -1], 'baseFret': 1, 'barres': []},
    'Gb7':     {'strings': [2, 4, 2, 3, 2, -1], 'baseFret': 1, 'barres': []},
    'F#maj7':  {'strings': [-1, -1, 4, 3, 2, 1], 'baseFret': 1, 'barres': []},
    'Gbmaj7':  {'strings': [-1, -1, 4, 3, 2, 1], 'baseFret': 1, 'barres': []},
    'F#m7':    {'strings': [2, 0, 2, 2, 2, 0], 'baseFret': 1, 'barres': []},
    'Gbm7':    {'strings': [2, 0, 2, 2, 2, 0], 'baseFret': 1, 'barres': []},
    'F#dim':   {'strings': [-1, -1, 4, 2, 1, 2], 'baseFret': 1, 'barres': []},
    'F#dim7':  {'strings': [-1, -1, 1, 2, 1, 2], 'baseFret': 1, 'barres': []},
    'F#aug':   {'strings': [-1, -1, 4, 3, 3, 2], 'baseFret': 1, 'barres': []},
    'F#sus4':  {'strings': [2, 4, 4, 4, 2, 2], 'baseFret': 1, 'barres': [2]},

    # === G 系列 ===
    'G':       {'strings': [3, 2, 0, 0, 0, 3], 'baseFret': 1, 'barres': []},
    'Gm':      {'strings': [3, 5, 5, 3, 3, 3], 'baseFret': 1, 'barres': [3]},
    'G7':      {'strings': [3, 2, 0, 0, 0, 1], 'baseFret': 1, 'barres': []},
    'Gmaj7':   {'strings': [3, 2, 0, 0, 0, 2], 'baseFret': 1, 'barres': []},
    'Gm7':     {'strings': [3, 5, 3, 3, 3, 3], 'baseFret': 1, 'barres': [3]},
    'Gdim':    {'strings': [3, 4, 5, 3, -1, -1], 'baseFret': 1, 'barres': []},
    'Gdim7':   {'strings': [-1, -1, 2, 3, 2, 3], 'baseFret': 1, 'barres': []},
    'Gaug':    {'strings': [3, 2, 1, 0, 0, 3], 'baseFret': 1, 'barres': []},
    'Gsus2':   {'strings': [3, 0, 0, 0, 3, 3], 'baseFret': 1, 'barres': []},
    'Gsus4':   {'strings': [3, 5, 5, 5, 3, 3], 'baseFret': 1, 'barres': [3]},
    'G6':      {'strings': [3, 2, 0, 0, 0, 0], 'baseFret': 1, 'barres': []},
    'Gm6':     {'strings': [-1, -1, 5, 3, 3, 0], 'baseFret': 1, 'barres': []},
    'G9':      {'strings': [3, 0, 0, 0, 0, 1], 'baseFret': 1, 'barres': []},
    'Gadd9':   {'strings': [3, 0, 0, 0, 0, 3], 'baseFret': 1, 'barres': []},

    # === G#/Ab 系列 ===
    'Ab':      {'strings': [4, 6, 6, 5, 4, 4], 'baseFret': 1, 'barres': [4]},
    'G#':      {'strings': [4, 6, 6, 5, 4, 4], 'baseFret': 1, 'barres': [4]},
    'Abm':     {'strings': [4, 6, 6, 4, 4, 4], 'baseFret': 1, 'barres': [4]},
    'G#m':     {'strings': [4, 6, 6, 4, 4, 4], 'baseFret': 1, 'barres': [4]},
    'Ab7':     {'strings': [-1, -1, 1, 1, 1, 2], 'baseFret': 4, 'barres': [1]},
    'G#7':     {'strings': [-1, -1, 1, 1, 1, 2], 'baseFret': 4, 'barres': [1]},
    'Abmaj7':  {'strings': [4, 6, 5, 5, 4, 4], 'baseFret': 1, 'barres': [4]},
    'G#maj7':  {'strings': [4, 6, 5, 5, 4, 4], 'baseFret': 1, 'barres': [4]},
    'Abm7':    {'strings': [-1, -1, 1, 1, 0, 2], 'baseFret': 4, 'barres': []},
    'G#m7':    {'strings': [-1, -1, 1, 1, 0, 2], 'baseFret': 4, 'barres': []},
    'Abdim':   {'strings': [4, 5, 6, 4, -1, -1], 'baseFret': 1, 'barres': []},
    'Abdim7':  {'strings': [-1, -1, 0, 1, 0, 1], 'baseFret': 1, 'barres': []},
    'Abaug':   {'strings': [-1, -1, 2, 1, 1, 0], 'baseFret': 1, 'barres': []},
    'Absus4':  {'strings': [4, 6, 6, 6, 4, 4], 'baseFret': 1, 'barres': [4]},

    # === A 系列 ===
    'A':       {'strings': [-1, 0, 2, 2, 2, 0], 'baseFret': 1, 'barres': []},
    'Am':      {'strings': [-1, 0, 2, 2, 1, 0], 'baseFret': 1, 'barres': []},
    'A7':      {'strings': [-1, 0, 2, 0, 2, 0], 'baseFret': 1, 'barres': []},
    'Amaj7':   {'strings': [-1, 0, 2, 1, 2, 0], 'baseFret': 1, 'barres': []},
    'Am7':     {'strings': [-1, 0, 2, 0, 1, 0], 'baseFret': 1, 'barres': []},
    'Adim':    {'strings': [-1, 0, 1, 2, 1, -1], 'baseFret': 1, 'barres': []},
    'Adim7':   {'strings': [-1, 0, 1, 2, 1, 2], 'baseFret': 1, 'barres': []},
    'Aaug':    {'strings': [-1, 0, 3, 2, 2, 1], 'baseFret': 1, 'barres': []},
    'Asus2':   {'strings': [-1, 0, 2, 2, 0, 0], 'baseFret': 1, 'barres': []},
    'Asus4':   {'strings': [-1, 0, 2, 2, 3, 0], 'baseFret': 1, 'barres': []},
    'A6':      {'strings': [-1, 0, 2, 2, 2, 2], 'baseFret': 1, 'barres': []},
    'Am6':     {'strings': [-1, 0, 2, 2, 1, 2], 'baseFret': 1, 'barres': []},
    'A9':      {'strings': [-1, 0, 2, 4, 2, 3], 'baseFret': 1, 'barres': []},
    'A6/9':    {'strings': [-1, 0, 4, 4, 2, 2], 'baseFret': 1, 'barres': []},
    'Aadd9':   {'strings': [-1, 0, 2, 2, 2, 2], 'baseFret': 1, 'barres': []},

    # === A#/Bb 系列 ===
    'Bb':      {'strings': [-1, 1, 3, 3, 3, 1], 'baseFret': 1, 'barres': [1]},
    'A#':      {'strings': [-1, 1, 3, 3, 3, 1], 'baseFret': 1, 'barres': [1]},
    'Bbm':     {'strings': [-1, 1, 3, 3, 2, 1], 'baseFret': 1, 'barres': [1]},
    'A#m':     {'strings': [-1, 1, 3, 3, 2, 1], 'baseFret': 1, 'barres': [1]},
    'Bb7':     {'strings': [-1, 1, 3, 1, 3, 1], 'baseFret': 1, 'barres': [1]},
    'Bbmaj7':  {'strings': [-1, 1, 3, 2, 3, 1], 'baseFret': 1, 'barres': [1]},
    'Bbm7':    {'strings': [-1, 1, 3, 1, 2, 1], 'baseFret': 1, 'barres': [1]},
    'Bbdim':   {'strings': [-1, 1, 2, 3, 2, -1], 'baseFret': 1, 'barres': []},
    'Bbdim7':  {'strings': [-1, -1, 2, 3, 2, 3], 'baseFret': 1, 'barres': []},
    'Bbaug':   {'strings': [-1, 1, 0, 3, 3, 2], 'baseFret': 1, 'barres': []},
    'Bbsus2':  {'strings': [-1, 1, 3, 3, 1, 1], 'baseFret': 1, 'barres': [1]},
    'Bbsus4':  {'strings': [-1, 1, 3, 3, 4, 1], 'baseFret': 1, 'barres': [1]},
    'Bb6':     {'strings': [-1, 1, 3, 3, 3, 3], 'baseFret': 1, 'barres': []},
    'Bb9':     {'strings': [-1, 1, 0, 1, 1, 1], 'baseFret': 1, 'barres': []},
    'Bb11':    {'strings': [-1, 1, 1, 1, 3, 1], 'baseFret': 1, 'barres': [1]},

    # === B 系列 ===
    'B':       {'strings': [-1, 2, 4, 4, 4, 2], 'baseFret': 1, 'barres': [2]},
    'Bm':      {'strings': [-1, 2, 4, 4, 3, 2], 'baseFret': 1, 'barres': [2]},
    'B7':      {'strings': [-1, 2, 1, 2, 0, 2], 'baseFret': 1, 'barres': []},
    'Bmaj7':   {'strings': [-1, 2, 4, 3, 4, 2], 'baseFret': 1, 'barres': [2]},
    'Bm7':     {'strings': [-1, 2, 0, 2, 0, 2], 'baseFret': 1, 'barres': []},
    'Bdim':    {'strings': [-1, 2, 3, 4, 3, -1], 'baseFret': 1, 'barres': []},
    'Bdim7':   {'strings': [-1, 2, 3, 1, 3, 1], 'baseFret': 1, 'barres': []},
    'Baug':    {'strings': [-1, 2, 1, 0, 0, 3], 'baseFret': 1, 'barres': []},
    'Bsus2':   {'strings': [-1, 2, 4, 4, 2, 2], 'baseFret': 1, 'barres': [2]},
    'Bsus4':   {'strings': [-1, 2, 4, 4, 5, 2], 'baseFret': 1, 'barres': [2]},
    'B6':      {'strings': [-1, 2, 4, 4, 4, 4], 'baseFret': 1, 'barres': []},
    'Bm6':     {'strings': [-1, 2, 0, 1, 0, 2], 'baseFret': 1, 'barres': []},
    'B9':      {'strings': [-1, 2, 1, 2, 2, 2], 'baseFret': 1, 'barres': []},

    # === 延伸和弦補充 ===
    'Eb11':    {'strings': [-1, -1, 1, 1, 2, 1], 'baseFret': 1, 'barres': [1]},
    'Cb':      {'strings': [-1, 2, 4, 4, 4, 2], 'baseFret': 1, 'barres': [2]},  # = B
    'F11':     {'strings': [1, 1, 1, 2, 1, 1], 'baseFret': 1, 'barres': [1]},
}

# 烏克麗麗和弦 (4弦, GCEA)
UKULELE_CHORDS = {
    'C':       {'strings': [0, 0, 0, 3], 'baseFret': 1, 'barres': []},
    'Cm':      {'strings': [0, 3, 3, 3], 'baseFret': 1, 'barres': [3]},
    'C7':      {'strings': [0, 0, 0, 1], 'baseFret': 1, 'barres': []},
    'Cmaj7':   {'strings': [0, 0, 0, 2], 'baseFret': 1, 'barres': []},
    'Cm7':     {'strings': [3, 3, 3, 3], 'baseFret': 1, 'barres': [3]},
    'D':       {'strings': [2, 2, 2, 0], 'baseFret': 1, 'barres': []},
    'Dm':      {'strings': [2, 2, 1, 0], 'baseFret': 1, 'barres': []},
    'D7':      {'strings': [2, 2, 2, 3], 'baseFret': 1, 'barres': []},
    'Dm7':     {'strings': [2, 2, 1, 3], 'baseFret': 1, 'barres': []},
    'E':       {'strings': [1, 4, 0, 2], 'baseFret': 1, 'barres': []},
    'Em':      {'strings': [0, 4, 3, 2], 'baseFret': 1, 'barres': []},
    'E7':      {'strings': [1, 2, 0, 2], 'baseFret': 1, 'barres': []},
    'Em7':     {'strings': [0, 2, 0, 2], 'baseFret': 1, 'barres': []},
    'F':       {'strings': [2, 0, 1, 0], 'baseFret': 1, 'barres': []},
    'Fm':      {'strings': [1, 0, 1, 3], 'baseFret': 1, 'barres': []},
    'F7':      {'strings': [2, 3, 1, 0], 'baseFret': 1, 'barres': []},
    'Fm7':     {'strings': [1, 3, 1, 3], 'baseFret': 1, 'barres': []},
    'G':       {'strings': [0, 2, 3, 2], 'baseFret': 1, 'barres': []},
    'Gm':      {'strings': [0, 2, 3, 1], 'baseFret': 1, 'barres': []},
    'G7':      {'strings': [0, 2, 1, 2], 'baseFret': 1, 'barres': []},
    'Gm7':     {'strings': [0, 2, 1, 1], 'baseFret': 1, 'barres': []},
    'A':       {'strings': [2, 1, 0, 0], 'baseFret': 1, 'barres': []},
    'Am':      {'strings': [2, 0, 0, 0], 'baseFret': 1, 'barres': []},
    'A7':      {'strings': [0, 1, 0, 0], 'baseFret': 1, 'barres': []},
    'Am7':     {'strings': [0, 0, 0, 0], 'baseFret': 1, 'barres': [0]},
    'Bb':      {'strings': [3, 2, 1, 1], 'baseFret': 1, 'barres': [1]},
    'Bbm':     {'strings': [3, 1, 1, 1], 'baseFret': 1, 'barres': [1]},
    'Bb7':     {'strings': [1, 2, 1, 1], 'baseFret': 1, 'barres': [1]},
    'B':       {'strings': [4, 3, 2, 2], 'baseFret': 1, 'barres': [2]},
    'Bm':      {'strings': [4, 2, 2, 2], 'baseFret': 1, 'barres': [2]},
    'B7':      {'strings': [2, 3, 2, 2], 'baseFret': 1, 'barres': [2]},
    'Eb':      {'strings': [0, 3, 3, 1], 'baseFret': 1, 'barres': []},
    'Ebm':     {'strings': [3, 3, 2, 1], 'baseFret': 1, 'barres': []},
    'Ab':      {'strings': [5, 3, 4, 3], 'baseFret': 1, 'barres': []},
    'Abm':     {'strings': [4, 3, 4, 2], 'baseFret': 1, 'barres': []},

    # === maj7 系列 ===
    'Dmaj7':   {'strings': [2, 2, 2, 4], 'baseFret': 1, 'barres': []},
    'Emaj7':   {'strings': [1, 3, 0, 2], 'baseFret': 1, 'barres': []},
    'Fmaj7':   {'strings': [2, 4, 1, 0], 'baseFret': 1, 'barres': []},
    'Gmaj7':   {'strings': [0, 2, 2, 2], 'baseFret': 1, 'barres': []},
    'Amaj7':   {'strings': [1, 1, 0, 0], 'baseFret': 1, 'barres': []},
    'Bmaj7':   {'strings': [4, 3, 2, 1], 'baseFret': 1, 'barres': []},
    'Bbmaj7':  {'strings': [3, 2, 1, 0], 'baseFret': 1, 'barres': []},
    'Ebmaj7':  {'strings': [3, 3, 3, 1], 'baseFret': 1, 'barres': [3]},
    'Abmaj7':  {'strings': [1, 3, 3, 3], 'baseFret': 1, 'barres': [3]},
    'C#maj7':  {'strings': [1, 1, 0, 1], 'baseFret': 1, 'barres': []},
    'Dbmaj7':  {'strings': [1, 1, 0, 1], 'baseFret': 1, 'barres': []},
    'F#maj7':  {'strings': [3, 5, 2, 1], 'baseFret': 1, 'barres': []},
    'Gbmaj7':  {'strings': [3, 5, 2, 1], 'baseFret': 1, 'barres': []},

    # === dim 系列 ===
    'Cdim':    {'strings': [0, 3, 2, 3], 'baseFret': 1, 'barres': []},
    'Ddim':    {'strings': [1, 2, 1, 2], 'baseFret': 1, 'barres': []},
    'Edim':    {'strings': [0, 4, 0, 1], 'baseFret': 1, 'barres': []},
    'Fdim':    {'strings': [1, 2, 1, 2], 'baseFret': 1, 'barres': []},
    'Gdim':    {'strings': [0, 1, 0, 1], 'baseFret': 1, 'barres': []},
    'Adim':    {'strings': [2, 3, 2, 3], 'baseFret': 1, 'barres': []},
    'Bdim':    {'strings': [1, 2, 1, 2], 'baseFret': 4, 'barres': []},
    'Bbdim':   {'strings': [0, 1, 0, 1], 'baseFret': 1, 'barres': []},
    'Ebdim':   {'strings': [2, 3, 2, 3], 'baseFret': 1, 'barres': []},
    'Abdim':   {'strings': [1, 2, 1, 2], 'baseFret': 1, 'barres': []},

    # === aug 系列 ===
    'Caug':    {'strings': [1, 0, 0, 3], 'baseFret': 1, 'barres': []},
    'Daug':    {'strings': [3, 2, 2, 1], 'baseFret': 1, 'barres': []},
    'Eaug':    {'strings': [1, 0, 0, 3], 'baseFret': 1, 'barres': []},
    'Faug':    {'strings': [2, 1, 1, 0], 'baseFret': 1, 'barres': []},
    'Gaug':    {'strings': [0, 3, 3, 2], 'baseFret': 1, 'barres': []},
    'Aaug':    {'strings': [2, 1, 1, 0], 'baseFret': 1, 'barres': []},
    'Baug':    {'strings': [0, 3, 3, 2], 'baseFret': 1, 'barres': []},

    # === sus 系列 ===
    'Csus2':   {'strings': [0, 2, 3, 3], 'baseFret': 1, 'barres': []},
    'Csus4':   {'strings': [0, 0, 1, 3], 'baseFret': 1, 'barres': []},
    'Dsus2':   {'strings': [2, 2, 0, 0], 'baseFret': 1, 'barres': []},
    'Dsus4':   {'strings': [0, 2, 3, 0], 'baseFret': 1, 'barres': []},
    'Esus4':   {'strings': [2, 4, 0, 2], 'baseFret': 1, 'barres': []},
    'Fsus2':   {'strings': [0, 0, 1, 0], 'baseFret': 1, 'barres': []},
    'Gsus2':   {'strings': [0, 2, 3, 0], 'baseFret': 1, 'barres': []},
    'Gsus4':   {'strings': [0, 2, 3, 3], 'baseFret': 1, 'barres': []},
    'Asus2':   {'strings': [2, 4, 0, 0], 'baseFret': 1, 'barres': []},
    'Asus4':   {'strings': [2, 2, 0, 0], 'baseFret': 1, 'barres': []},

    # === 6 和弦 ===
    'C6':      {'strings': [0, 0, 0, 0], 'baseFret': 1, 'barres': []},
    'D6':      {'strings': [2, 2, 2, 2], 'baseFret': 1, 'barres': [2]},
    'E6':      {'strings': [4, 4, 4, 4], 'baseFret': 1, 'barres': [4]},
    'F6':      {'strings': [2, 2, 1, 3], 'baseFret': 1, 'barres': []},
    'G6':      {'strings': [0, 2, 0, 2], 'baseFret': 1, 'barres': []},
    'A6':      {'strings': [2, 4, 2, 4], 'baseFret': 1, 'barres': []},

    # === 9 和弦 ===
    'C9':      {'strings': [0, 2, 0, 1], 'baseFret': 1, 'barres': []},
    'D9':      {'strings': [2, 4, 2, 3], 'baseFret': 1, 'barres': []},
    'G9':      {'strings': [2, 2, 1, 2], 'baseFret': 1, 'barres': []},
    'A9':      {'strings': [0, 1, 0, 2], 'baseFret': 1, 'barres': []},

    # === 更多 7 和弦（補齊升降號） ===
    'Eb7':     {'strings': [3, 3, 3, 4], 'baseFret': 1, 'barres': [3]},
    'Ab7':     {'strings': [1, 3, 2, 3], 'baseFret': 1, 'barres': []},
    'Abm7':    {'strings': [4, 3, 4, 4], 'baseFret': 1, 'barres': []},
    'C#7':     {'strings': [1, 1, 1, 2], 'baseFret': 1, 'barres': [1]},
    'Db7':     {'strings': [1, 1, 1, 2], 'baseFret': 1, 'barres': [1]},
    'C#m7':    {'strings': [1, 1, 0, 2], 'baseFret': 1, 'barres': []},
    'Dbm7':    {'strings': [1, 1, 0, 2], 'baseFret': 1, 'barres': []},
    'F#':      {'strings': [3, 1, 2, 1], 'baseFret': 1, 'barres': [1]},
    'F#m':     {'strings': [2, 1, 2, 0], 'baseFret': 1, 'barres': []},
    'F#7':     {'strings': [3, 4, 2, 1], 'baseFret': 1, 'barres': []},
    'F#m7':    {'strings': [2, 4, 2, 4], 'baseFret': 1, 'barres': []},
    'Gb':      {'strings': [3, 1, 2, 1], 'baseFret': 1, 'barres': [1]},
    'Gbm':     {'strings': [2, 1, 2, 0], 'baseFret': 1, 'barres': []},
    'Gbm7':    {'strings': [2, 4, 2, 4], 'baseFret': 1, 'barres': []},
    'Bbm7':    {'strings': [1, 1, 1, 1], 'baseFret': 1, 'barres': [1]},
    'Bm7':     {'strings': [2, 2, 2, 2], 'baseFret': 1, 'barres': [2]},
    'Ebm7':    {'strings': [3, 3, 2, 4], 'baseFret': 1, 'barres': []},
}


def get_chord_diagram(chord_name, instrument='guitar'):
    """取得和弦圖資料"""
    db = GUITAR_CHORDS if instrument == 'guitar' else UKULELE_CHORDS
    num_strings = 6 if instrument == 'guitar' else 4

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

    return None


def list_available_chords(instrument='guitar'):
    """列出可用的和弦"""
    db = GUITAR_CHORDS if instrument == 'guitar' else UKULELE_CHORDS
    return sorted(db.keys())
