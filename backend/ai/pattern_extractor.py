import re

# 定義 12 半音的對應 (以 C 為 0)
NOTES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
FLAT_NOTES = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]

def note_to_semi(note):
    note = note.upper()
    if note in NOTES: return NOTES.index(note)
    if note in FLAT_NOTES: return FLAT_NOTES.index(note)
    return 0

# 將半音差轉換為大調羅馬數字級數
ROMAN_NUMERALS = {
    0: "I", 1: "bII", 2: "II", 3: "bIII", 4: "III", 5: "IV",
    6: "bV", 7: "V", 8: "bVI", 9: "VI", 10: "bVII", 11: "VII"
}

class PatternExtractor:
    """
    和弦特徵萃取器 (Chord Pattern Extractor)
    用於在混合式 AI 架構中，將絕對和弦轉換為級數，並辨識經典的樂理 Pattern (如 ii-V-I)。
    """
    def __init__(self):
        # 這裡定義常見的樂句 Pattern Regex，以簡易的 Regex 比對 Roman Numeral 字串
        # \S* 用以匹配任何額外的後綴 (例如 m7, maj7, 7b9) 而不會不小心跨越空白
        self.patterns = {
            "251_major": r"\bii\S*\s+V\S*\s+I\S*",
            "251_minor": r"\bii(?:dim|m7b5)\S*\s+V\S*\s+i\S*",
            "turnaround": r"\bI\S*\s+vi\S*\s+ii\S*\s+V\S*",
            "modal_interchange": r"\bIV\S*\s+iv\S*\s+I\S*",
            "tritone_sub": r"\bii\S*\s+bII\S*\s+I\S*",
            "line_cliche": r"\bi\b\S*\s+i(?:maj7|#7)\S*\s+i7\S*\s+i6\S*" 
        }

    def chord_to_roman(self, chord, key="C"):
        """將絕對和弦 (e.g. Dm7) 轉為相對調的級數 (e.g. iim7)"""
        if not chord or chord == "N": return "N"
        
        # 分離根音與和弦品質
        m = re.match(r"^([A-G][b#]?)(.*)$", chord)
        if not m:
            return chord
            
        root, quality = m.group(1), m.group(2)
        
        root_semi = note_to_semi(root)
        key_semi = note_to_semi(key)
        
        # 計算相對於 Key 的半音音程
        interval = ((root_semi - key_semi) % 12 + 12) % 12
        roman = ROMAN_NUMERALS[interval]
        
        # 大小調級數大小寫處理 (Minor chords 轉小寫羅馬字)
        if quality.startswith("m") and not quality.startswith("maj"):
            roman = roman.lower()
        elif quality == "dim" or quality == "dim7" or quality.startswith("m7b5"):
            # 減和弦通常用小寫
            roman = roman.lower()
            
        return f"{roman}{quality}"

    def sequence_to_romans(self, chords, key="C"):
        """將整個和弦序列轉化為級數"""
        return [self.chord_to_roman(c, key) for c in chords]

    def extract_patterns(self, chords, key="C"):
        """
        找尋連續和弦序列中符合的 Pattern，返回找到的特徵片段。
        """
        romans = self.sequence_to_romans(chords, key)
        # 用空白串接以便 Regex 搜尋，例如 "iim7 V7 Imaj7"
        roman_str = " ".join(romans)
        
        results = []
        
        for name, regex in self.patterns.items():
            # 尋找所有 Match
            matches = re.finditer(regex, roman_str, re.IGNORECASE)
            for match in matches:
                matched_text = match.group()
                
                # 回推在原始陣列中的 index
                prefix = roman_str[:match.start()]
                start_idx = len(prefix.strip().split()) if prefix.strip() else 0
                matched_tokens = matched_text.strip().split()
                end_idx = start_idx + len(matched_tokens)
                
                if end_idx <= len(chords):
                    results.append({
                        "pattern": name,
                        "start_idx": start_idx,
                        "end_idx": end_idx - 1,
                        "matched_chords": chords[start_idx:end_idx],
                        "matched_romans": romans[start_idx:end_idx]
                    })
                    
        # 按照發現的位置排序
        results.sort(key=lambda x: x["start_idx"])
        return results

if __name__ == "__main__":
    # 簡單的測試案例
    extractor = PatternExtractor()
    test_progression = ["Cmaj7", "Am7", "Dm7", "G7", "Cmaj7", "Fmaj7", "Fm7", "Cmaj7"]
    test_key = "C"
    
    print(f"Original Chords: {test_progression}")
    print(f"Key: {test_key}")
    print(f"Romans: {extractor.sequence_to_romans(test_progression, test_key)}")
    print("\n--- Extracted Patterns ---")
    
    found = extractor.extract_patterns(test_progression, test_key)
    for p in found:
        print(f"[{p['pattern']}] idx {p['start_idx']}~{p['end_idx']}: {p['matched_chords']} ({' '.join(p['matched_romans'])})")
