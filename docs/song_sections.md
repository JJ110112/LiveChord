# 歌曲段落結構定義

本文件定義歌曲中常見的段落類型、出現位置及其功能。
LiveChord 的 `section_detect.py` 依據這些定義進行自動段落偵測。

---

| 段落名稱 | 英文名稱 | 常見位置 | 功能說明 |
|---|---|---|---|
| **前奏** | Intro / Prelude | 歌曲最前端 | 建立歌曲第一印象，提供聲音訊息讓歌手抓 Key |
| **主歌** | Verse | 前奏之後 | 講述故事背景的段落，鋪陳情節與情緒 |
| **導歌** | Pre-Chorus | 主歌與副歌之間 | 主歌到副歌的情緒連結與過渡 |
| **副歌** | Chorus | 主歌或導歌之後 | 歌曲最重要、最精彩的核心段落 |
| **間奏** | Interlude | 副歌之後 | 段落間的中場休息，或安排樂器獨奏 |
| **橋段** | Bridge | 兩次副歌之間 | 兩次副歌間的情緒緩衝，提供對比與變化 |
| **尾奏** | Outro / Postlude | 歌曲最後段 | 總結聽歌情緒，為歌曲收尾 |

---

## 典型段落排列

```
Intro → Verse → Pre-Chorus → Chorus → Interlude → Verse → Pre-Chorus → Chorus → Bridge → Chorus → Outro
```

## 段落與伴奏參數對應

LiveChord 的 `accompaniment_generator.py` 依據段落類型自動調整伴奏參數：

| 段落 | 密度 (Density) | 力度 (Velocity) | 建議 Pattern | 能量等級 |
|---|---|---|---|---|
| Intro | 50% | 60% | Block（稀疏） | 低 |
| Verse | 70% | 70% | Arpeggio（流動） | 中 |
| Pre-Chorus | 80% | 80% | Rhythm（推進） | 中高 |
| Chorus | 100% | 100% | Rhythm（全能量） | 高 |
| Interlude | 50% | 55% | Arpeggio / Shell | 低 |
| Bridge | 60% | 65% | Arpeggio（對比） | 中低 |
| Outro | 40% | 50% | Block（漸弱） | 低 |
