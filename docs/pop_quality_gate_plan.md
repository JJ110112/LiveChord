# POP Quality Gate 與下一輪測試計畫

## 為何要重新跑

`reports/` 裡的舊報表多數產生於全局仲裁完成前，而且 `scripts/quality_gate.py` 曾漏掉 `global_chord_arbiter`。因此舊報表只能看趨勢，不能作為目前品質結論。

2026-05-12 已修正 quality gate：

- serve pipeline 與 player API 對齊。
- 評分使用 `display_bpm`，而不是只用 stored `bpm`。
- 小圓點評估尊重 `display_beats`。

## 建議重跑命令

快速 POP/全庫抽樣：

```powershell
python scripts\quality_gate.py --library-root Z:/ --data-root V:/data --sample 1000 --out-dir reports/quality_gate_pop_global_displaybpm_20260512
```

全量回歸，適合階段結束前跑：

```powershell
python scripts\quality_gate.py --library-root Z:/ --data-root V:/data --sample 0 --out-dir reports/quality_gate_full_global_displaybpm_20260512
```

## 2026-05-12 抽樣結果

抽樣 1000 首，排除 `classics` 與 `sleep`，類別權重 POP 50%、Jazz 25%、其他 25%。

POP 結果：

- Sampled: 500 / POP library tracks: 23020
- Pass rate: 0.884
- Severe rate: 0.116
- Avg visible fragments: 0.468
- 主要失敗型態：`long_cards`，其次是少量 `fragments` 與 legacy `midi` source。

解讀：

- 主觀 QA 的改善非常明顯：Night Birds、過完冬季、Making Love Out of Nothing at All、Lover、Merry-Go-Round、APT.、獨上西樓、夜曲、小酒窩、Perfect Duet 都已把核心 4/4/2+2 顯示問題大幅修掉。
- 量化 gate 仍未通過，因為它現在開始抓到更廣泛的 POP 長卡與特殊曲型，而不是只抓單曲 bug。
- POP 已從「很多歌局部破碎」進到「大部分一般 POP 可讀，剩下是 long-card/generalization backlog」階段。

## 下一輪優化排序

1. POP long-card repair
   - 對 `long_cards` 為主、`bad_fragments=0` 的歌曲先處理。
   - 這代表小圓點不一定亂，但卡片超過 4 拍未切，違反 player 規格。

2. Half-bar display mismatch
   - 例如 `half-bar-4-dots`：duration 接近 2 拍，但顯示 4 點。
   - 這類通常是 display_beats 被全局文法推太強，應降級成 2 拍或重新合併鄰卡。

3. Stable loop induction
   - 從多首 POP 抽出固定 loop，例如 `I V vi IV`、`vi V IV I`、`I(2) V(2) IV(4)`。
   - 讓仲裁器先判斷「整段循環文法」，再決定局部卡片拍數。

4. Legacy MIDI cleanup
   - `source=midi` 不再視為 gold。
   - 對仍是 MIDI source 的歌曲，應優先使用 BTC/beat_this 或重新 detect。

5. 非 POP 延後
   - Jazz、Relax、Christmas 目前 gate 不佳，但不應用 POP 規則硬修。
   - 這些類別要另立 jazz/rubato/acoustic quality gate。

## 階段完成門檻

POP 本階段建議門檻：

- POP pass rate >= 0.95
- POP severe rate <= 0.02
- POP avg visible fragments <= 1.0
- 全局 `tail_gap_rate <= 0.01`
- 抽樣 failure top 30 中不再出現同一 pattern 大量重複。

達成後可宣布「POP 4/4 仲裁階段完成」，再進入 Jazz/Rubato/Acoustic 專項。

