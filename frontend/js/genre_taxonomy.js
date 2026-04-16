// Genre taxonomy: maps normalized (lowercase, trimmed) genre name → big category key.
// Used by admin.html to organise the skip-genre checkboxes hierarchically.
// Anything not in the map falls into "misc" (其他 / 雜項).

(function (w) {
  const CATEGORY_ORDER = [
    "pop", "rock", "rnb", "hiphop", "jazz", "classical",
    "electronic", "world", "ambient", "ost", "holiday", "misc",
  ];

  const CATEGORY_LABEL = {
    pop:        "流行 (Pop)",
    rock:       "搖滾 (Rock)",
    rnb:        "R&B / 靈魂",
    hiphop:     "嘻哈 / 說唱",
    jazz:       "爵士 (Jazz)",
    classical:  "古典 (Classical)",
    electronic: "電子 / 舞曲",
    world:      "民謠 / 世界",
    ambient:    "氛圍 / 放鬆",
    ost:        "動畫 / 原聲",
    holiday:    "節慶 (Holiday)",
    misc:       "其他 / 雜項",
  };

  const GENRE_MAP = {
    // ===== 流行 (Pop) =====
    "pop": "pop",
    "j-pop": "pop", "jpop": "pop",
    "k-pop": "pop", "k-ballad": "pop",
    "mandopop": "pop", "city pop": "pop", "cantopop": "pop",
    "taiwanese pop": "pop", "kayokyoku": "pop",
    "ballad": "pop", "soft pop": "pop", "art pop": "pop",
    "acoustic pop": "pop", "adult contemporary": "pop", "adult standards": "pop",
    "easy listening": "pop", "folk pop": "pop",
    "c-pop": "pop", "dance pop": "pop", "bedroom pop": "pop",
    "baroque pop": "pop", "pop country": "pop",
    "power pop": "pop", "german pop": "pop", "norwegian pop": "pop",
    "t-pop": "pop", "thai pop": "pop", "swedish pop": "pop", "britpop": "pop",
    "latin pop": "pop", "opm": "pop",
    "italian singer-songwriter": "pop", "singer-songwriter": "pop",
    "shibuya-kei": "pop", "chanson": "pop",
    "variete francaise": "pop", "variété française": "pop",
    "gufeng": "pop", "jangle pop": "pop",

    // ===== 搖滾 (Rock) =====
    "rock": "rock", "indie rock": "rock", "classic rock": "rock",
    "soft rock": "rock", "country rock": "rock", "k-rock": "rock",
    "chinese rock": "rock", "brazilian rock": "rock", "j-rock": "rock",
    "hard rock": "rock", "glam rock": "rock", "glam metal": "rock",
    "visual kei": "rock", "shoegaze": "rock",
    "post-rock": "rock", "post-punk": "rock", "folk rock": "rock",
    "surf rock": "rock", "yacht rock": "rock", "arena rock": "rock",
    "art rock": "rock", "blues rock": "rock", "grunge": "rock",
    "rockabilly": "rock", "new wave": "rock", "lo-fi indie": "rock",
    "indie": "rock", "japanese indie": "rock", "taiwanese indie": "rock",
    "chinese indie": "rock", "pinoy indie": "rock",
    "anti-folk": "rock", "southern gothic": "rock", "aor": "rock",

    // ===== R&B / 靈魂 =====
    "r&b": "rnb", "j-r&b": "rnb", "chinese r&b": "rnb", "uk r&b": "rnb",
    "alternative r&b": "rnb",
    "soul": "rnb", "neo soul": "rnb", "retro soul": "rnb", "trap soul": "rnb",
    "quiet storm": "rnb", "motown": "rnb", "doo-wop": "rnb",
    "philly soul": "rnb", "new jack swing": "rnb", "pop soul": "rnb",
    "funk": "rnb",

    // ===== 嘻哈 / 說唱 =====
    "rap": "hiphop", "j-rap": "hiphop", "k-rap": "hiphop",
    "chinese hip hop": "hiphop", "mexican hip hop": "hiphop",
    "old school hip hop": "hiphop", "east coast hip hop": "hiphop",
    "melodic rap": "hiphop", "drill": "hiphop", "jazz rap": "hiphop",
    "argentine trap": "hiphop", "chilean trap": "hiphop",
    "lo-fi hip hop": "hiphop", "lo-fi beats": "hiphop",
    "freestyle": "hiphop",

    // ===== 爵士 (Jazz) =====
    "jazz": "jazz", "vocal jazz": "jazz", "jazz fusion": "jazz",
    "acid jazz": "jazz", "nu jazz": "jazz", "smooth jazz": "jazz",
    "french jazz": "jazz", "brazilian jazz": "jazz", "jazz beats": "jazz",
    "free jazz": "jazz", "jazz blues": "jazz", "indie jazz": "jazz",
    "soul jazz": "jazz", "big band": "jazz", "ragtime": "jazz",

    // ===== 古典 (Classical) =====
    "classics": "classical", "classical": "classical",
    "classical piano": "classical", "neoclassical": "classical",
    "choral": "classical", "opera": "classical",
    "chamber music": "classical", "symphony": "classical",
    "orchestra": "classical", "japanese classical": "classical",
    "medieval": "classical", "minimalism": "classical",
    "requiem": "classical", "classical music": "classical",

    // ===== 電子 / 舞曲 =====
    "electronic dance music": "electronic", "edm": "electronic",
    "drum and bass": "electronic", "dance": "electronic",
    "disco": "electronic", "disco house": "electronic",
    "tribal house": "electronic", "future bass": "electronic",
    "hard house": "electronic", "big beat": "electronic",
    "synthwave": "electronic", "chillstep": "electronic",
    "french house": "electronic", "stutter house": "electronic",
    "trance": "electronic", "hardstyle": "electronic",
    "electro swing": "electronic", "footwork": "electronic",
    "indie electronic": "electronic", "downtempo": "electronic",
    "melodic bass": "electronic", "moombahton": "electronic",
    "tropical house": "electronic", "post-disco": "electronic",
    "jazz house": "electronic", "electro corridos": "electronic",

    // ===== 民謠 / 世界 =====
    "folk": "world", "indie folk": "world", "ambient folk": "world",
    "christian folk": "world", "folklore": "world", "celtic": "world",
    "tango": "world", "bossa nova": "world", "samba": "world",
    "mpb": "world", "nova mpb": "world", "enka": "world",
    "reggaeton": "world", "reggaeton chileno": "world",
    "reggae": "world", "spanish-language reggae": "world", "nz reggae": "world",
    "dancehall": "world", "dembow": "world",
    "cumbia norteña": "world", "bolero": "world", "banda": "world",
    "corrido": "world", "corridos tumbados": "world",
    "trap latino": "world", "urbano latino": "world",
    "latin afrobeats": "world", "afrobeats": "world",
    "bluegrass": "world", "blues": "world",
    "country": "world", "classic country": "world",
    "americana": "world", "alt country": "world",
    "tropical music": "world", "flamenco urbano": "world",
    "native american music": "world", "bollywood": "world",
    "latin": "world", "cuarteto": "world",
    "techengue": "world", "rkt": "world",
    "cha cha cha": "world",
    "ska": "world", "dub": "world",
    "canzone napoletana": "world", "fado": "world",
    "laiko": "world", "laïko": "world",
    "entehno": "world",
    "worship": "world", "christian": "world",

    // ===== 氛圍 / 放鬆 =====
    "relax": "ambient", "sleep": "ambient", "lullaby": "ambient",
    "lo-fi": "ambient", "lounge": "ambient",
    "ambient": "ambient", "dark ambient": "ambient",
    "new age": "ambient", "space music": "ambient",
    "drone": "ambient", "exotica": "ambient",

    // ===== 動畫 / 原聲 =====
    "anime": "ost", "soundtrack": "ost", "vocaloid": "ost",
    "film & animation": "ost", "musicals": "ost",
    "children's music": "ost", "children'S music": "ost",

    // ===== 節慶 =====
    "christmas": "holiday",

    // ===== 其他 / 雜項 =====
    "other": "misc", "music": "misc", "jam": "misc",
    "entertainment": "misc", "people & blogs": "misc",
    "comedy": "misc", "education": "misc",
    "sports": "misc", "news & politics": "misc",
    "travel & events": "misc", "check it": "misc",
    "spoken word": "misc",
  };

  function categorize(genreName) {
    if (!genreName) return "misc";
    const key = String(genreName).toLowerCase().trim();
    return GENRE_MAP[key] || "misc";
  }

  w.LC_GENRE_TAXONOMY = {
    CATEGORY_ORDER: CATEGORY_ORDER,
    CATEGORY_LABEL: CATEGORY_LABEL,
    GENRE_MAP: GENRE_MAP,
    categorize: categorize,
  };
})(window);
