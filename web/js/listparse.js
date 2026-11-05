/* Shopping-list reader. Pure functions, no DOM. Browser: window.ListParse. Node: require().
 *
 * Turns free text such as "beras 10kg, telur 2 papan, minyak masak 5kg, ayam 2 ekor" into
 * basket lines {code, qty, alt?}. Deterministic: a concept table for common words in BM and
 * English, then a plain word search over item names as a fallback. The shopper confirms each match.
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.ListParse = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  // ---------- concepts ----------
  // k: words people write (BM, English, short forms). re: which item names belong to the concept.
  // flex: the shopper usually does not mind the brand, so "any brand" starts switched on.
  // size: default pack when the list gives none. kgEach: kg per "ekor" (whole bird).
  var CONCEPTS = [
    { id: "beras", k: ["beras", "rice", "beras putih", "white rice"], re: /^BERAS (?!PULUT)/, flex: true, size: "10 kg" },
    { id: "pulut", k: ["pulut", "beras pulut", "glutinous rice", "pulut rice"], re: /^BERAS PULUT/, flex: true },
    { id: "ayam", k: ["ayam", "chicken", "ayam bersih", "whole chicken", "ayam standard", "ayam super"], re: /^AYAM BERSIH/, prefer: /STANDARD/, kgEach: 1.5 },
    { id: "dada", k: ["dada ayam", "chicken breast", "isi ayam", "breast"], re: /^DADA AYAM/ },
    { id: "kepak", k: ["kepak ayam", "kepak", "chicken wing", "chicken wings", "wings"], re: /^KEPAK AYAM/ },
    { id: "paha", k: ["paha ayam", "peha ayam", "drumstick", "drumsticks", "chicken drumstick"], re: /^PAHA AYAM/ },
    { id: "thigh", k: ["thigh", "thigh ayam", "chicken thigh", "peha atas"], re: /^THIGH AYAM/ },
    { id: "leg", k: ["whole leg", "whole leg ayam", "chicken leg", "kaki ayam penuh"], re: /^WHOLE LEG AYAM/ },
    { id: "telur", k: ["telur", "telor", "telo", "egg", "eggs", "telur ayam"], re: /^TELUR AYAM GRED/, prefer: /GRED A/ },
    { id: "gula", k: ["gula", "sugar", "gula putih", "white sugar", "gula kasar"], re: /^GULA PUTIH/, prefer: /KASAR/ },
    { id: "gula-halus", k: ["gula halus", "caster sugar", "fine sugar"], re: /^GULA PUTIH BERTAPIS HALUS/ },
    { id: "gula-merah", k: ["gula merah", "gula perang", "brown sugar"], re: /^GULA MERAH/ },
    { id: "minyak", k: ["minyak masak", "minyak", "cooking oil", "oil", "minyak sawit", "palm oil"], re: /^MINYAK MASAK (SEBATIAN|TULEN)/, flex: true, size: "5 kg" },
    { id: "minyak-paket", k: ["minyak paket", "minyak masak paket", "minyak subsidi", "oil packet"], re: /^MINYAK MASAK PAKET/ },
    { id: "minyak-jagung", k: ["minyak jagung", "corn oil"], re: /^MINYAK JAGUNG/, flex: true },
    { id: "minyak-sapi", k: ["minyak sapi", "ghee"], re: /^MINYAK SAPI/, flex: true },
    { id: "tepung", k: ["tepung", "tepung gandum", "flour", "wheat flour", "plain flour"], re: /^TEPUNG GANDUM/, flex: true },
    { id: "tepung-naik", k: ["tepung naik sendiri", "self raising flour", "self-raising flour"], re: /^TEPUNG NAIK SENDIRI/ },
    { id: "bawang-besar", k: ["bawang", "bawang besar", "onion", "onions", "big onion", "bawang holland"], re: /^BAWANG BESAR/, flex: true },
    { id: "bawang-merah", k: ["bawang merah", "bawang kecil", "shallot", "shallots", "small onion"], re: /^BAWANG KECIL MERAH/, flex: true },
    { id: "bawang-putih", k: ["bawang putih", "garlic"], re: /^BAWANG PUTIH/ },
    { id: "bawang-perai", k: ["bawang perai", "leek", "leeks"], re: /^BAWANG PERAI/, flex: true },
    { id: "kentang", k: ["kentang", "ubi kentang", "potato", "potatoes"], re: /^UBI KENTANG/, flex: true },
    { id: "susu-pekat", k: ["susu pekat", "susu manis", "krimer", "krimer manis", "condensed milk", "susu pekat manis", "creamer"], re: /^KRIMER MANIS/, flex: true },
    { id: "susu-segar", k: ["susu segar", "fresh milk", "susu", "milk"], re: /^(SUSU SEGAR DUTCH LADY|DESA FRESH MILK|SUSU MARIGOLD HL)/, flex: true },
    { id: "susu-tepung", k: ["susu tepung", "milk powder", "powdered milk"], re: /^SUSU TEPUNG/, flex: true },
    { id: "susu-bayi", k: ["susu bayi", "susu formula", "formula", "baby milk", "infant formula"], cat: "SUSU BAYI" },
    { id: "lampin", k: ["lampin", "lampin pakai buang", "diaper", "diapers", "pampers", "nappy", "nappies"], cat: "LAMPIN PAKAI BUANG" },
    { id: "makanan-bayi", k: ["makanan bayi", "baby food", "cerelac", "bubur bayi"], cat: "MAKANAN BAYI" },
    { id: "sardin", k: ["sardin", "sardine", "sardines", "sardin tin"], re: /^SARDIN/, flex: true },
    { id: "santan", k: ["santan", "coconut milk", "santan segar"], re: /^SANTAN KELAPA SEGAR \(BIASA\)/ },
    { id: "santan-pekat", k: ["santan pekat"], re: /^SANTAN KELAPA SEGAR \(PEKAT\)/ },
    { id: "santan-kotak", k: ["santan kotak", "!kara", "santan kara", "coconut milk box", "!harmuni"], re: /^SANTAN KELAPA JENAMA/, flex: true },
    { id: "kelapa-parut", k: ["kelapa parut", "grated coconut"], re: /^KELAPA PARUT/ },
    { id: "kelapa", k: ["kelapa", "coconut"], re: /^KELAPA BIJI/ },
    { id: "kicap", k: ["kicap", "kicap manis", "soy sauce", "sweet soy sauce"], re: /^KICAP/, flex: true },
    { id: "sos-cili", k: ["sos cili", "sos cil", "chilli sauce", "chili sauce"], re: /^SOS CILI/, flex: true },
    { id: "sos-tomato", k: ["sos tomato", "ketchup", "tomato sauce", "tomato ketchup"], re: /^SOS TOMATO/ },
    { id: "sos-tiram", k: ["sos tiram", "oyster sauce"], re: /^SOS TIRAM/ },
    { id: "bihun", k: ["bihun", "mihun", "meehoon", "mee hoon", "bee hoon", "vermicelli", "rice vermicelli"], re: /^BIHUN/, flex: true },
    { id: "mee", k: ["mee", "mi", "mee kuning", "mi kuning", "yellow noodles", "noodles"], re: /^MEE KUNING/ },
    { id: "maggi", k: ["maggi", "mi segera", "mee segera", "instant noodles", "maggi kari"], re: /^MAGGI MI SEGERA/ },
    { id: "milo", k: ["milo"], re: /^MILO/ },
    { id: "kopi", k: ["kopi", "coffee", "!nescafe", "!indocafe", "kopi o"], re: /^(NESCAFE CLASSIC|INDOCAFE)/, flex: true },
    { id: "kopi-3in1", k: ["nescafe 3in1", "3in1", "kopi 3in1", "nescafe 3 in 1"], re: /^NESCAFE 3 IN 1/ },
    { id: "teh", k: ["teh", "tea", "teh uncang", "teabag", "tea bags", "!boh", "!lipton"], re: /^TEH /, flex: true },
    { id: "oat", k: ["oat", "oats", "oatmeal", "quaker"], re: /^QUAKER OATS/ },
    { id: "garam", k: ["garam", "salt"], re: /^GARAM/ },
    { id: "kangkung", k: ["kangkung", "water spinach"], re: /^KANGKUNG/ },
    { id: "bayam", k: ["bayam", "spinach", "bayam hijau"], re: /^BAYAM/, prefer: /HIJAU/ },
    { id: "sawi", k: ["sawi", "sawi hijau", "choy sum", "choysum", "mustard greens"], re: /^SAWI HIJAU/ },
    { id: "pak-choy", k: ["pak choy", "bok choy", "siew pak choy", "sawi jepun", "sawi pendek"], re: /^SAWI PENDEK/ },
    { id: "kailan", k: ["kailan", "kai lan", "chinese broccoli"], re: /^KAILAN/ },
    { id: "kubis", k: ["kubis", "kobis", "cabbage", "kubis bulat"], re: /^KUBIS BULAT/, flex: true },
    { id: "kubis-bunga", k: ["kubis bunga", "kobis bunga", "cauliflower"], re: /^KUBIS BUNGA/ },
    { id: "kubis-panjang", k: ["kubis panjang", "sawi putih", "chinese cabbage", "napa cabbage", "wong bok"], re: /^KUBIS PANJANG/, flex: true },
    { id: "brokoli", k: ["brokoli", "broccoli"], re: /^BROKOLI/ },
    { id: "tomato", k: ["tomato", "tomatoes", "tomatos"], re: /^TOMATO$/ },
    { id: "timun", k: ["timun", "cucumber", "cucumbers"], re: /^TIMUN/ },
    { id: "lobak-merah", k: ["lobak merah", "carrot", "carrots", "lobak"], re: /^LOBAK MERAH/ },
    { id: "lobak-putih", k: ["lobak putih", "radish", "white radish", "daikon"], re: /^LOBAK PUTIH/ },
    { id: "terung", k: ["terung", "terong", "brinjal", "eggplant", "aubergine"], re: /^TERUNG/, flex: true },
    { id: "kacang-panjang", k: ["kacang panjang", "long bean", "long beans"], re: /^KACANG PANJANG/ },
    { id: "bendi", k: ["bendi", "kacang bendi", "okra", "lady finger", "ladies finger", "lady's finger"], re: /^KACANG BENDI/ },
    { id: "buncis", k: ["buncis", "kacang buncis", "french bean", "french beans"], re: /^KACANG BUNCIS/ },
    { id: "kacang-botol", k: ["kacang botol", "four angled bean", "winged bean"], re: /^KACANG BOTOL/ },
    { id: "saderi", k: ["saderi", "celery"], re: /^SADERI/ },
    { id: "capsicum", k: ["capsicum", "lada benggala", "bell pepper"], re: /^LADA BENGGALA/ },
    { id: "cili", k: ["cili", "cili merah", "chilli", "chili", "chillies", "red chilli", "cabai", "lada"], re: /^CILI MERAH/, flex: true },
    { id: "cili-padi", k: ["cili padi", "cili api", "bird eye chilli", "bird's eye chilli", "chili padi"], re: /^CILI API/ },
    { id: "cili-hijau", k: ["cili hijau", "green chilli", "green chili"], re: /^CILI HIJAU/ },
    { id: "cili-akar", k: ["cili akar", "cili akar merah"], re: /^CILI AKAR/, prefer: /MERAH/ },
    { id: "cili-kering", k: ["cili kering", "dried chilli", "dried chili", "dry chilli"], re: /^CILI KERING/, flex: true },
    { id: "halia", k: ["halia", "ginger"], re: /^HALIA/ },
    { id: "kunyit", k: ["kunyit", "turmeric", "kunyit hidup"], re: /^KUNYIT HIDUP/ },
    { id: "lengkuas", k: ["lengkuas", "galangal"], re: /^LENGKUAS/ },
    { id: "limau-nipis", k: ["limau nipis", "lime", "limes"], re: /^LIMAU NIPIS/ },
    { id: "limau-kasturi", k: ["limau kasturi", "calamansi", "kasturi"], re: /^LIMAU KASTURI/ },
    { id: "ikan-kembung", k: ["ikan kembung", "kembung", "mackerel", "indian mackerel"], re: /^IKAN KEMBUNG \(/ },
    { id: "pelaling", k: ["pelaling", "kembung kecil"], re: /^IKAN KEMBUNG KECIL/ },
    { id: "selar", k: ["ikan selar", "selar", "pelata"], re: /^IKAN SELAR/, flex: true },
    { id: "selayang", k: ["ikan selayang", "selayang", "ikan sardin", "fresh sardine"], re: /^IKAN SELAYANG/ },
    { id: "cencaru", k: ["ikan cencaru", "cencaru", "hardtail scad"], re: /^IKAN CENCARU/ },
    { id: "tongkol", k: ["ikan tongkol", "tongkol", "ikan aya", "tuna", "aya"], re: /^IKAN TONGKOL/ },
    { id: "tenggiri", k: ["ikan tenggiri", "tenggiri", "spanish mackerel"], re: /^IKAN TENGGIRI/ },
    { id: "bawal", k: ["ikan bawal", "bawal", "pomfret"], re: /^IKAN BAWAL/, flex: true },
    { id: "siakap", k: ["ikan siakap", "siakap", "seabass", "sea bass", "barramundi"], re: /^IKAN SIAKAP/ },
    { id: "tilapia", k: ["ikan tilapia", "tilapia", "tilapia merah"], re: /^IKAN TILAPIA/ },
    { id: "keli", k: ["ikan keli", "keli", "catfish"], re: /^IKAN KELI/ },
    { id: "kerisi", k: ["ikan kerisi", "kerisi", "threadfin bream"], re: /^IKAN KERISI/ },
    { id: "gelama", k: ["ikan gelama", "gelama", "croaker"], re: /^IKAN GELAMA/ },
    { id: "ikan-merah", k: ["ikan merah", "red snapper", "snapper"], re: /^IKAN MERAH/ },
    { id: "jenahak", k: ["ikan jenahak", "jenahak"], re: /^IKAN JENAHAK/ },
    { id: "udang", k: ["udang", "prawn", "prawns", "shrimp", "shrimps", "udang putih"], re: /^UDANG PUTIH/, prefer: /VANNAMEI/ },
    { id: "udang-harimau", k: ["udang harimau", "tiger prawn", "tiger prawns"], re: /^UDANG HARIMAU/ },
    { id: "udang-kering", k: ["udang kering", "dried shrimp", "dried shrimps", "ebi"], re: /^UDANG KERING/ },
    { id: "sotong", k: ["sotong", "squid", "sotong segar"], re: /^SOTONG \(/ },
    { id: "sotong-kering", k: ["sotong kering", "dried squid"], re: /^SOTONG KERING/ },
    { id: "ketam", k: ["ketam", "crab", "crabs", "ketam bunga"], re: /^KETAM/ },
    { id: "daging", k: ["daging", "daging lembu", "beef", "lembu"], re: /^DAGING LEMBU/, prefer: /IMPORT \(BLOCK\)/ },
    { id: "kerbau", k: ["daging kerbau", "kerbau", "buffalo meat", "daging india"], re: /^DAGING KERBAU/, flex: true },
    { id: "kambing", k: ["kambing", "daging kambing", "mutton", "lamb"], re: /^DAGING (PAHA )?KAMBING/, prefer: /TEMPATAN/ },
    { id: "babi", k: ["babi", "daging babi", "pork", "pork belly", "babi perut"], re: /^DAGING BABI/ },
    { id: "pisang", k: ["pisang", "banana", "bananas"], re: /^PISANG/, flex: true },
    { id: "betik", k: ["betik", "papaya"], re: /^BETIK/ },
    { id: "tembikai", k: ["tembikai", "watermelon", "semangka"], re: /^TEMBIKAI MERAH/, flex: true },
    { id: "nenas", k: ["nenas", "nanas", "pineapple"], re: /^NENAS/ },
    { id: "epal", k: ["epal", "apple", "apples"], re: /^EPAL/ },
    { id: "anggur", k: ["anggur", "grape", "grapes"], re: /^ANGGUR/, flex: true },
    { id: "jambu", k: ["jambu", "jambu batu", "guava"], re: /^JAMBU BATU/, flex: true },
    { id: "naga", k: ["buah naga", "dragon fruit", "naga"], re: /^DRAGON FRUIT/ },
    { id: "lai", k: ["lai", "pear", "pir"], re: /^LAI KUNING/ },
    { id: "kacang-dal", k: ["kacang dal", "dal", "dhal", "lentil", "lentils"], re: /^KACANG DAL/, flex: true },
    { id: "kacang-hijau", k: ["kacang hijau", "mung bean", "mung beans", "green beans dry"], re: /^KACANG HIJAU/ },
    { id: "kacang-merah", k: ["kacang merah", "red bean", "red beans", "kidney beans"], re: /^KACANG MERAH/ },
    { id: "kacang-tanah", k: ["kacang tanah", "peanut", "peanuts", "groundnut"], re: /^KACANG TANAH/ },
    { id: "kacang-soya", k: ["kacang soya", "soybean", "soybeans", "soya bean"], re: /^KACANG SOYA/ },
    { id: "mentega", k: ["mentega", "butter", "anchor"], re: /^MENTEGA ANCHOR/ },
    { id: "marjerin", k: ["marjerin", "margarine", "!planta", "!daisy"], re: /^MARJERIN/, flex: true },
    { id: "kacang-sapuan", k: ["mentega kacang", "peanut butter"], re: /^MENTEGA KACANG/ },
    { id: "jem", k: ["jem", "jam", "strawberry jam"], re: /^JEM/ },
    { id: "mayo", k: ["mayonis", "mayonnaise", "mayo"], re: /^MAYONIS/ },
    { id: "keju", k: ["keju", "cheese", "cheese spread", "chesdale"], re: /CHEESE/ },
    { id: "kari", k: ["serbuk kari", "kari", "curry powder", "rempah kari", "curry"], re: /^SERBUK KARI/, flex: true },
    { id: "kari-ikan", k: ["kari ikan", "serbuk kari ikan", "fish curry powder"], re: /^SERBUK KARI IKAN/, flex: true },
    { id: "serbuk-cili", k: ["serbuk cili", "cili serbuk", "chilli powder", "chili powder"], re: /^SERBUK CILI/ },
    { id: "serbuk-kunyit", k: ["serbuk kunyit", "turmeric powder"], re: /^SERBUK KUNYIT/ },
    { id: "asam-jawa", k: ["asam jawa", "asam", "tamarind"], re: /^ASAM JAWA/, flex: true },
    { id: "kiub", k: ["kiub", "stock cube", "kiub tom yam", "tom yam cube"], re: /^KIUB/ },
    { id: "tomyam", k: ["perencah tom yam", "tom yam", "tomyam", "tom yum paste"], re: /^PERENCAH TOM YAM/ },
    { id: "sup", k: ["serbuk sup", "perencah sup", "soup powder", "rempah sup"], re: /^SERBUK PERENCAH SUP/ },
    { id: "nasi-goreng", k: ["perencah nasi goreng", "nasi goreng paste", "serbuk nasi goreng"], re: /NASI GORENG/, flex: true },
    { id: "baking", k: ["serbuk penaik", "baking powder"], re: /^SERBUK PENAIK/ },
    { id: "esen", k: ["esen", "essence", "pewarna"], re: /^ESEN/ },
    { id: "biji-sawi", k: ["biji sawi", "mustard seed", "mustard seeds"], re: /^BIJI SAWI/ },
    { id: "sabun-serbuk", k: ["sabun basuh", "sabun serbuk", "serbuk pencuci", "detergent", "washing powder", "laundry", "sabun baju", "!breeze", "!daia", "!attack", "!top"], re: /^SABUN (SERBUK|DETERGEN|DAIA)/ },
    { id: "sabun-pinggan", k: ["sabun pinggan", "pencuci pinggan", "dishwash", "dish soap", "sabun cuci pinggan", "!sunlight", "!axion", "!harimau"], re: /^SABUN PENCUCI/ },
    { id: "sabun-mandi", k: ["sabun mandi", "shower gel", "body wash", "sabun mandian"], re: /^SABUN MANDIAN/, flex: true },
    { id: "syampu", k: ["syampu", "shampoo", "sampu", "!rejoice", "!sunsilk", "!head & shoulders"], re: /^SYAMPU/ },
    { id: "ubat-gigi", k: ["ubat gigi", "toothpaste", "!colgate", "!darlie"], re: /^UBAT GIGI/ },
    { id: "berus-gigi", k: ["berus gigi", "toothbrush"], re: /^BERUS GIGI/ },
    { id: "pelembut", k: ["pelembut", "pelembut pakaian", "softener", "fabric softener", "downy"], re: /^PELEMBUT/ },
    { id: "peluntur", k: ["peluntur", "clorox", "bleach"], re: /^PELUNTUR/ },
    { id: "pencuci-lantai", k: ["pencuci lantai", "floor cleaner", "pencuci pelbagai guna", "cleaner", "!ajax", "!mr muscle"], re: /^PENCUCI PELBAGAI GUNA/, flex: true },
    { id: "deodoran", k: ["deodoran", "deodorant"], re: /^DEODORAN/ },
    { id: "100plus", k: ["100 plus", "100plus", "isotonic"], re: /^100 PLUS/ },
    { id: "coke", k: ["coca cola", "coke", "cola"], re: /^COCA COLA/ },
    { id: "pepsi", k: ["pepsi"], re: /^PEPSI/ },
    { id: "fn-oren", k: ["f&n oren", "fn oren", "orange soda", "air oren"], re: /^F&N OREN/ },
    { id: "kordial", k: ["kordial", "cordial", "sirap", "syrup", "!sunquick"], re: /^KORDIAL/ },
    { id: "jus-oren", k: ["jus oren", "orange juice"], re: /^JUS OREN/ },
    { id: "soya", k: ["air soya", "soya", "soy milk", "soya milk"], re: /SOYA \(KOTAK\)/, flex: true },
    { id: "yogurt", k: ["yogurt", "yoghurt", "yogurt strawberry"], re: /^YOGURT/, flex: true },
    { id: "red-bull", k: ["red bull"], re: /^RED BULL/ },
    { id: "livita", k: ["livita"], re: /^LIVITA/ },
    { id: "coffee-mate", k: ["coffee mate", "coffee-mate", "creamer powder"], re: /COFFEE-MATE/ }
  ];

  // ---------- text helpers ----------
  // Words that say nothing about the item ("barang dapur minggu ni", "need to buy").
  var FILLER = /^(barang|barangan|dapur|minggu|mingguan|ni|ini|tu|itu|list|senarai|shopping|grocery|groceries|beli|belanja|kedai|pasar|untuk|nak|need|buy|get|weekly|bulan|month|monthly|items?|things|stuff|lagi|more|sikit|sedikit|banyak|dan|and|&|the|of|for|yang|dgn|dengan|to|jangan|lupa|dont|forget|please|tolong|also|juga)$/;

  function norm(s) {
    return String(s || "").toLowerCase().normalize("NFKD").replace(/[̀-ͯ]/g, "")
      .replace(/&/g, " & ").replace(/[^a-z0-9&.,'\s-]/g, " ").replace(/\s+/g, " ").trim();
  }
  function words(s) { return norm(s).replace(/[.,'-]/g, " ").split(/\s+/).filter(Boolean); }

  var NUMWORDS = { satu: 1, se: 1, one: 1, a: 1, dua: 2, two: 2, tiga: 3, three: 3, empat: 4, four: 4,
                   lima: 5, five: 5, enam: 6, six: 6, tujuh: 7, seven: 7, lapan: 8, eight: 8,
                   sembilan: 9, nine: 9, sepuluh: 10, ten: 10, setengah: 0.5, separuh: 0.5, half: 0.5 };
  var UNITS = {
    kg: ["kg", "kgs", "kilo", "kilos", "kilogram", "kilograms", "kilogramme"],
    g: ["g", "gm", "gr", "gram", "grams", "gramme"],
    l: ["l", "liter", "litre", "liters", "litres", "lt", "ltr"],
    ml: ["ml", "mililiter", "millilitre", "milliliter"],
    biji: ["biji", "bijik", "pcs", "pc", "piece", "pieces", "keping", "buah", "nos"],
    papan: ["papan", "tray", "trays", "dulang"],
    ekor: ["ekor", "whole", "birds"],
    pek: ["pek", "paket", "packet", "packets", "pack", "packs", "pkt", "bungkus", "bks", "beg", "bag", "bags",
          "tin", "tins", "can", "cans", "botol", "bottle", "bottles", "btl", "kotak", "box", "boxes", "kotak2",
          "x", "unit", "units", "ikat", "bundle", "set", "balang", "jar", "jars", "tube", "batang", "uncang"]
  };
  var UNIT_OF = {};
  Object.keys(UNITS).forEach(function (u) { UNITS[u].forEach(function (w) { UNIT_OF[w] = u; }); });
  // "sekilo", "sepapan", "sebotol"... = 1 of that unit
  function seWord(w) {
    if (w.length > 3 && w.indexOf("se") === 0 && UNIT_OF[w.slice(2)]) return UNIT_OF[w.slice(2)];
    if (w === "sekilo") return "kg";
    return null;
  }

  /**
   * Split pasted text into lines. Splits on new lines, commas, semicolons and bullets.
   * Commas inside numbers ("1,5 kg") are kept.
   */
  function splitLines(text) {
    return String(text || "")
      .replace(/\r/g, "\n")
      .replace(/(\d),(\d)/g, "$1.$2")
      .split(/\n|;|,|•|·|•|\|/)
      .map(function (l) {
        return l.replace(/^\s*(\[[ xX✓✔]?\]|[☐☑✅✔✓*+\->]|\d{1,2}[.)](?!\d))+\s*/, "")
                .replace(/\brm\s*\d+(\.\d+)?\b/gi, " ")   // prices people jot next to items
                .replace(/\s+/g, " ").trim();
      })
      .filter(function (l) { return l && /[a-zA-Z]/.test(l) && !/:\s*$/.test(l); });   // "Barang dapur:" is a heading
  }

  /**
   * Read the amount from one line.
   * Returns {words: [remaining words], n: number|null, unit: "kg"|"g"|"l"|"ml"|"biji"|"papan"|"ekor"|"pek"|null}
   */
  function parseQty(line) {
    var s = norm(line)
      .replace(/\b100\s*plus\b/g, "100plus").replace(/\b3\s*in\s*1\b/g, "3in1")
      .replace(/(\d)\s*(kg|g|gm|gr|ml|l|ltr|lt|kilo|biji|pcs|x)\b/g, "$1 $2")   // 10kg -> 10 kg
      .replace(/\bx\s*(\d)/g, "x $1")
      .replace(/(\d)\s*x\b/g, "$1 x");
    var toks = s.replace(/[,']/g, " ").split(/\s+/).filter(Boolean);
    var n = null, unit = null, rest = [], i;
    for (i = 0; i < toks.length; i++) {
      var w = toks[i], num = /^\d+(\.\d+)?$/.test(w) ? Number(w) : null;
      if (num == null && /^\d+\/\d+$/.test(w)) { var f = w.split("/"); num = Number(f[0]) / Number(f[1]); }
      if (num == null && NUMWORDS[w] != null && (UNIT_OF[toks[i + 1]] || /^(kilo|kg)$/.test(toks[i + 1] || "")) && w !== "a" && w !== "se") num = NUMWORDS[w];
      if (num != null && n == null) {
        var nx = toks[i + 1];
        if (nx === "x") { n = num; unit = "pek"; i++; continue; }           // "2 x milo"
        if (nx && UNIT_OF[nx]) { n = num; unit = UNIT_OF[nx]; i++; continue; }
        n = num; continue;                                                 // bare number
      }
      if (w === "x" && toks[i + 1] && /^\d+(\.\d+)?$/.test(toks[i + 1]) && n == null) { n = Number(toks[i + 1]); unit = "pek"; i++; continue; }
      var se = seWord(w);
      if (se && n == null) { n = 1; unit = se; continue; }
      if (unit == null && n != null && UNIT_OF[w] && w !== "x") { unit = UNIT_OF[w]; continue; }
      if (w === "half" && toks[i + 1] === "kg") { n = 0.5; unit = "kg"; i++; continue; }
      rest.push(w);
    }
    // "setengah kilo" / "half kg" after other words
    return { words: rest, n: n, unit: unit };
  }

  // ---------- catalogue ----------
  /**
   * Prepare the item list once. items: [{code, item, unit, item_group, item_category}]
   * cover(code) -> number of shops pricing the item here (used to break ties).
   */
  function Catalogue(items, cover) {
    this.items = items;
    this.cover = cover || function () { return 0; };
    this.byCode = new Map();
    var self = this;
    items.forEach(function (it) {
      var u = parseUnitLite(it.unit);
      self.byCode.set(it.code, { it: it, size: u, hay: " " + words(it.item + " " + it.item_category + " " + it.item_group).join(" ") + " " });
    });
    // Longest keys first so "bawang putih" wins over "bawang".
    this.keys = [];
    CONCEPTS.forEach(function (c) {
      c.k.forEach(function (k) {
        var brand = k.charAt(0) === "!";
        self.keys.push({ k: words(brand ? k.slice(1) : k), c: c, brand: brand });
      });
    });
    this.keys.sort(function (a, b) { return b.k.length - a.k.length || b.k.join(" ").length - a.k.join(" ").length; });
  }

  function parseUnitLite(u) {
    // Same rules as Basket.parseUnit, repeated so this file has no dependency.
    if (!u) return null;
    var s = String(u).toLowerCase().replace(/\+-|±/g, "").replace(/,/g, ".").trim();
    var mult = 1, mm = s.match(/^(\d+)\s*x\s*(.+)$/);
    if (mm) { mult = Number(mm[1]); s = mm[2]; }
    var m = s.match(/^(\d+(?:\.\d+)?)\s*(kg|g|gm|liter|litre|l|ml|biji|beg|batang|pcs)$/);
    if (!m) return null;
    var n = Number(m[1]) * mult, unit = m[2];
    if (unit === "kg") return { amt: n * 1000, base: "g" };
    if (unit === "g" || unit === "gm") return { amt: n, base: "g" };
    if (unit === "liter" || unit === "litre" || unit === "l") return { amt: n * 1000, base: "ml" };
    if (unit === "ml") return { amt: n, base: "ml" };
    return { amt: n, base: "pc" };
  }

  function sameSize(a, b) {
    return a && b && a.base === b.base && Math.abs(a.amt - b.amt) <= Math.max(a.amt, b.amt) * 0.06;
  }

  // Edit distance with a cut-off, for typos such as "bawng" or "kangkong".
  function near(a, b) {
    if (a === b) return true;
    if (a.length < 5 || b.length < 4 || Math.abs(a.length - b.length) > 1) return false;
    var prev = [], cur, i, j;
    for (j = 0; j <= b.length; j++) prev[j] = j;
    for (i = 1; i <= a.length; i++) {
      cur = [i];
      for (j = 1; j <= b.length; j++) {
        cur[j] = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
      }
      prev = cur;
    }
    return prev[b.length] <= 1;
  }

  // A word appears in an item's text: whole word for 1-2 letters ("gred b"), word start otherwise.
  function hasWord(hay, w) {
    if (w.length <= 2) return hay.indexOf(" " + w + " ") >= 0;
    return hay.indexOf(" " + w) >= 0;
  }

  /** Find the concept named in the words, allowing one typo per word. Returns {c, used:[indexes]} or null. */
  Catalogue.prototype.findConcept = function (ws) {
    for (var x = 0; x < this.keys.length; x++) {
      var key = this.keys[x].k;
      for (var s = 0; s + key.length <= ws.length; s++) {
        var ok = true;
        for (var t = 0; t < key.length; t++) {
          var uw = ws[s + t];
          if (FILLER.test(uw) ? uw !== key[t] : !near(uw, key[t])) { ok = false; break; }
        }
        if (ok) {
          var used = [];
          for (var u = 0; u < key.length; u++) used.push(s + u);
          return { c: this.keys[x].c, used: used, brand: this.keys[x].brand ? this.keys[x].k : null };
        }
      }
    }
    return null;
  };

  Catalogue.prototype.conceptItems = function (c) {
    var out = [];
    this.byCode.forEach(function (v) {
      if (c.cat ? v.it.item_category === c.cat : c.re.test(v.it.item)) out.push(v);
    });
    return out;
  };

  /** Word search over item names (fallback). Returns [{v, score}] best first. */
  Catalogue.prototype.search = function (ws) {
    var self = this, out = [];
    ws = ws.filter(function (w) { return !FILLER.test(w); });
    if (!ws.length) return out;
    this.byCode.forEach(function (v) {
      var hit = 0;
      ws.forEach(function (w) {
        if (w.length < 2) return;
        if (hasWord(v.hay, w)) hit += 1;
        else if (w.length >= 5 && v.hay.split(" ").some(function (h) { return near(w, h); })) hit += 0.8;
      });
      if (hit > 0) out.push({ v: v, score: hit / ws.length + Math.min(self.cover(v.it.code), 50) / 1000 });
    });
    out.sort(function (a, b) { return b.score - a.score || a.v.it.item.localeCompare(b.v.it.item); });
    return out;
  };

  function packsFor(want, size) {
    // How many packs of `size` cover `want` (same base). Fresh 1 kg items allow halves.
    var r = want / size.amt;
    if (size.base === "g" && size.amt === 1000) return Math.max(0.5, Math.round(r * 2) / 2);
    return Math.max(1, Math.ceil(r - 0.03));
  }

  /**
   * Match one line of a shopping list.
   * Returns {raw, status: "ok"|"pick"|"none", code, alt:[codes], qty, flex, options:[codes], note}
   *   ok:   a confident match. pick: a guess, the shopper should check. none: nothing found.
   */
  Catalogue.prototype.matchLine = function (raw) {
    var q = parseQty(raw), ws = q.words, self = this;
    var res = { raw: raw, status: "none", code: null, alt: [], qty: 1, flex: false, options: [], note: "" };
    if (!ws.length) return res;

    var found = this.findConcept(ws), cands, extra;
    // "santan 2 kotak" means boxed santan.
    if (found && found.c.id === "santan" && /\b(kotak|box|boxes|pek|paket)\b/.test(norm(raw))) {
      found = { c: CONCEPTS.filter(function (c) { return c.id === "santan-kotak"; })[0], used: found.used };
    }
    if (found) {
      cands = this.conceptItems(found.c);
      if (found.brand) {
        var br = cands.filter(function (v) { return found.brand.every(function (w) { return hasWord(v.hay, w); }); });
        if (br.length) cands = br;
      }
      extra = ws.filter(function (_, i) { return found.used.indexOf(i) < 0; })
                .filter(function (w) { return !/^(dan|and|of|the|untuk|for|yang|nak|beli|buy|lagi|more)$/.test(w); });
    } else {
      var hits = this.search(ws);
      if (!hits.length || hits[0].score < 0.5) return res;
      var top = hits[0].score;
      cands = hits.filter(function (h) { return h.score >= top - 0.05; }).slice(0, 12).map(function (h) { return h.v; });
      res.options = hits.slice(0, 8).map(function (h) { return h.v.it.code; });
      extra = [];
    }
    if (!cands.length) return res;

    // Extra words narrow the choice (brand, grade, origin): "beras jasmine", "telur gred b", "minyak saji".
    if (extra.length) {
      var narrowed = cands.filter(function (v) {
        return extra.every(function (w) { return hasWord(v.hay, w) || (w.length >= 5 && v.hay.split(" ").some(function (h) { return near(w, h); })); });
      });
      if (narrowed.length) cands = narrowed;
      else {
        var some = cands.filter(function (v) { return extra.some(function (w) { return hasWord(v.hay, w); }); });
        if (some.length) cands = some;
      }
    }

    // Wanted amount, in the item's base unit where possible.
    var want = null;
    if (found && found.c.id === "telur" && q.unit == null && q.n != null && q.n >= 6) q.unit = "biji";
    if (q.unit === "kg" && q.n != null) want = { amt: q.n * 1000, base: "g" };
    else if (q.unit === "g" && q.n != null) want = { amt: q.n, base: "g" };
    else if (q.unit === "l" && q.n != null) want = { amt: q.n * 1000, base: "ml" };
    else if (q.unit === "ml" && q.n != null) want = { amt: q.n, base: "ml" };
    else if (q.unit === "papan") want = { amt: (q.n || 1) * 30, base: "pc", eggs: true };
    else if (q.unit === "biji" && found && found.c.id === "telur") want = { amt: q.n || 1, base: "pc", eggs: true };
    else if (q.unit === "ekor" && found && found.c.kgEach) {
      want = { amt: (q.n || 1) * found.c.kgEach * 1000, base: "g" };
      res.note = "ekor";
    }

    // Choose the pack size.
    var pool = cands, chosenSize = null, exactHit = false;
    if (want) {
      var exact = cands.filter(function (v) { return sameSize(v.size, want); });
      if (!exact.length) {
        // Close enough to one pack (e.g. "Milo 1kg" vs the 900 g pack): treat as one pack.
        exact = cands.filter(function (v) { return v.size && v.size.base === want.base && Math.abs(v.size.amt - want.amt) <= want.amt * 0.15 && v.size.amt !== 1000; });
      }
      if (exact.length) { pool = exact; chosenSize = exact[0].size; want = { amt: chosenSize.amt, base: chosenSize.base }; exactHit = true; }
      else {
        // Biggest pack that divides the amount evenly, else the 1 kg / per-kg item, else the biggest pack below it.
        var fit = cands.filter(function (v) { return v.size && v.size.base === want.base && v.size.amt <= want.amt + 1e-9; })
                       .sort(function (a, b) { return b.size.amt - a.size.amt; });
        var even = fit.filter(function (v) { var r = want.amt / v.size.amt; return Math.abs(r - Math.round(r)) < 0.03; });
        var pickF = even[0] || fit[0];
        if (pickF) { chosenSize = pickF.size; pool = cands.filter(function (v) { return sameSize(v.size, chosenSize); }); }
        else {
          var up = cands.filter(function (v) { return v.size && v.size.base === want.base; })
                        .sort(function (a, b) { return a.size.amt - b.size.amt; });
          if (up[0]) { chosenSize = up[0].size; pool = cands.filter(function (v) { return sameSize(v.size, chosenSize); }); }
        }
      }
    }
    if (!chosenSize && found && found.c.size) {
      var def = parseUnitLite(found.c.size), dp = cands.filter(function (v) { return sameSize(v.size, def); });
      if (dp.length) { pool = dp; chosenSize = def; }
    }
    if (!chosenSize) {
      // Most widely priced size among the candidates.
      var groups = [];
      cands.forEach(function (v) {
        var g = groups.filter(function (x) { return v.size ? sameSize(x.size, v.size) : x.unit === v.it.unit; })[0];
        if (!g) { g = { size: v.size, unit: v.it.unit, n: 0, list: [] }; groups.push(g); }
        g.n += self.cover(v.it.code) + 0.001; g.list.push(v);
      });
      groups.sort(function (a, b) { return b.n - a.n; });
      pool = groups[0].list; chosenSize = groups[0].size;
    }

    // Main item: preferred variant, then the most widely priced.
    pool = pool.slice().sort(function (a, b) {
      var pa = found && found.c.prefer && found.c.prefer.test(a.it.item) ? 1 : 0;
      var pb = found && found.c.prefer && found.c.prefer.test(b.it.item) ? 1 : 0;
      return (pb - pa) || (self.cover(b.it.code) - self.cover(a.it.code)) || a.it.item.localeCompare(b.it.item);
    });
    var main = pool[0];
    res.code = main.it.code;
    res.flex = !!(found && found.c.flex && pool.length > 1 && !extra.length && !found.brand);
    res.alt = pool.slice(1).map(function (v) { return v.it.code; });
    if (!res.options.length) {
      res.options = pool.map(function (v) { return v.it.code; })
        .concat(cands.filter(function (v) { return pool.indexOf(v) < 0; }).map(function (v) { return v.it.code; })).slice(0, 12);
    }

    // Quantity in packs.
    if (want && chosenSize && chosenSize.base === want.base) res.qty = packsFor(want.amt, chosenSize);
    else if (q.n != null && (q.unit === "pek" || q.unit === "biji" || q.unit == null)) res.qty = q.n;
    else if (q.n != null && q.unit === "ekor") res.qty = q.n;
    res.qty = Math.max(0.5, Math.min(50, res.qty));
    if (chosenSize && !(chosenSize.base === "g" && chosenSize.amt === 1000)) res.qty = Math.max(1, Math.round(res.qty));

    res.status = found && (cands.length === 1 || found.c.flex || found.c.prefer || extra.length || found.brand) ? "ok" : "pick";
    if (want && chosenSize && !exactHit && !(chosenSize.base === "g" && chosenSize.amt === 1000) && !res.note) {
      var ratio = want.amt / chosenSize.amt;
      if (chosenSize.base !== want.base || Math.abs(ratio - Math.round(ratio)) > 0.03 || ratio < 1) res.note = "size";
    }
    return res;
  };

  /** Match every line of a pasted list. */
  Catalogue.prototype.matchText = function (text) {
    var self = this;
    return splitLines(text).map(function (l) { return self.matchLine(l); });
  };

  return { Catalogue: Catalogue, splitLines: splitLines, parseQty: parseQty, CONCEPTS: CONCEPTS, near: near };
});
