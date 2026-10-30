"""English and Turkish strings.

English is the default whatever the system locale. Turkish is one click away
in the title bar (remembered in settings.json) or `maze-scrub --lang=tr`.
`t()` falls back to English, then to the key.
"""
from __future__ import annotations

STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "bug_body": "This was a bug, not something you did. Your files were not changed; "
                    "the details are in the system journal.",
        # chrome
        "tip_minimize": "Minimize",
        "tip_maximize": "Maximize / Restore",
        "tip_close": "Close",
        "tip_language": "Language",
        # hero
        "eyebrow": "METADATA CLEANER",
        "hero_title": "See what your files say about you.\nThen make them stop.",
        "hero_sub": "Photos carry where they were taken and on which phone. Documents carry "
                    "your name, your company and when you edited them. Scrub shows you "
                    "every hidden field first, then removes them with mat2.",
        "btn_add_files": "Add files",
        "btn_add_folder": "Add folder",
        "drop_hint": "or drop files and folders anywhere in this window",
        # no mat2
        "no_mat2_title": "mat2 is not installed",
        "no_mat2_body": "Maze Scrub uses mat2 to read and remove metadata. Install it in a "
                        "terminal, then press Check again:",
        "no_python_body": "The system Python (/usr/bin/python3) that mat2 runs on is missing.",
        "btn_recheck": "Check again",
        "missing_tools": "Optional tools not found: {names}. Some formats (video, some "
                         "images) cannot be cleaned without them.",
        # list
        "sec_files": "FILES",
        "btn_clear": "Clear list",
        "empty_title": "Nothing here yet",
        "empty_body": "Add a photo, a PDF or an office document to see what it reveals.",
        "pill_scanning": "READING",
        "pill_fields": "{n} FIELDS",
        "pill_one_field": "1 FIELD",
        "pill_none": "NO METADATA",
        "pill_unsupported": "NOT SUPPORTED",
        "pill_error": "ERROR",
        "pill_cleaned": "CLEANED",
        "pill_failed": "FAILED",
        "tip_remove": "Remove from the list",
        "btn_details": "Show fields",
        "btn_hide": "Hide fields",
        "reveals": "Reveals: {cats}",
        "found_before": "IN THE ORIGINAL",
        "cat_location": "Location",
        "cat_person": "Personal",
        "cat_device": "Device & IDs",
        "cat_time": "Dates",
        "cat_software": "Software",
        "unsupported_body": "mat2 cannot read this kind of file, so Scrub leaves it alone.",
        "error_body": "Could not read this file: {detail}",
        # results
        "res_copy": "Cleaned copy: {name}",
        "res_replaced": "Cleaned in place. The original is in the Trash.",
        "res_left": "Still inside after cleaning: {keys}",
        "res_none_left": "Nothing left — mat2 found no metadata in the cleaned file.",
        "res_rewritten": "New values written by the cleaner, not taken from your file: {keys}",
        "e_readonly_dir": "This folder is read-only, so no cleaned file could be written.",
        "e_clean_failed": "mat2 could not clean this file. It was not changed.",
        "e_trash_failed": "The original could not be moved to the Trash, so it was kept. "
                          "The cleaned file is next to it: {name}",
        "e_changed": "The file changed while it was being cleaned, so it was not replaced. "
                     "The cleaned version is next to it: {name}",
        "e_rename_failed": "The original is in the Trash, but the cleaned file could not be "
                           "put in its place. It is here: {name}",
        # action bar
        "mode_copy": "Save a cleaned copy next to each file",
        "mode_copy_hint": "Your originals stay exactly as they are.",
        "mode_replace": "Replace the originals",
        "mode_replace_hint": "Each original goes to the Trash, so you can still restore it.",
        "lightweight": "Lightweight mode",
        "lightweight_hint": "Keeps PDFs searchable and images untouched, but removes less. "
                            "Leave it off unless you need that.",
        "btn_clean": "Clean {n} files",
        "btn_clean_one": "Clean 1 file",
        "btn_clean_none": "Clean",
        "cleaning": "Cleaning {done} of {total}…",
        "reading": "Reading {done} of {total}…",
        # limits
        "sec_limits": "WHAT SCRUB CANNOT DO",
        "limit_content": "It removes data about a file, not what is in it: faces, a street "
                         "sign, a reflection or a name written in a document stay visible.",
        "limit_pdf": "PDFs are re-drawn as images to remove everything, so their text can no "
                     "longer be selected or searched (unless Lightweight mode is on).",
        "limit_images": "Images are re-saved, which can slightly change JPEG quality.",
        "limit_identity": "Your files may still identify you in other ways — writing style, "
                          "camera sensor noise, or the account you send them from.",
        # status
        "status_ready": "{n} files in the list",
        "status_empty": "Add files to begin",
        "status_done": "{ok} cleaned, {failed} failed",
        "status_truncated": "Stopped at {n} files — add fewer at a time.",
        "confirm_replace_title": "Replace the originals?",
        "confirm_replace_body": "{n} files will be cleaned in place. The originals go to the "
                                "Trash, where you can restore them until you empty it.",
        "btn_replace": "Replace",
        "btn_cancel": "Cancel",
        "e_helper": "mat2 failed: {detail}",
        "e_cancelled": "Stopped before this file was cleaned. It was not changed.",
        "btn_stop": "Stop",
        "status_stopped": "Stopped. Files already done keep their results.",
        "pill_stopped": "NOT READ",
        "not_read": "Not read — you stopped before Scrub got to this file. Add it again to read it.",
        "e_timeout": "mat2 took too long and was stopped.",
    },
    "tr": {
        "bug_body": "Bu bir hataydı, sizin yaptığınız bir şey değil. Dosyalarınız "
                    "değiştirilmedi; ayrıntılar sistem günlüğünde.",
        "tip_minimize": "Küçült",
        "tip_maximize": "Büyüt / Geri al",
        "tip_close": "Kapat",
        "tip_language": "Dil",
        "eyebrow": "METADATA TEMİZLEYİCİ",
        "hero_title": "Dosyalarınız sizin hakkınızda ne anlatıyor?\nGörün, sonra susturun.",
        "hero_sub": "Fotoğraflar nerede ve hangi telefonla çekildiğini taşır. Belgeler "
                    "adınızı, şirketinizi ve ne zaman düzenlediğinizi taşır. Scrub önce her "
                    "gizli alanı gösterir, sonra mat2 ile siler.",
        "btn_add_files": "Dosya ekle",
        "btn_add_folder": "Klasör ekle",
        "drop_hint": "ya da dosya ve klasörleri bu pencerenin herhangi bir yerine bırakın",
        "no_mat2_title": "mat2 kurulu değil",
        "no_mat2_body": "Maze Scrub, metadata'yı okumak ve silmek için mat2 kullanır. Bir "
                        "terminalde kurun, sonra Tekrar kontrol et'e basın:",
        "no_python_body": "mat2'nin çalıştığı sistem Python'u (/usr/bin/python3) bulunamadı.",
        "btn_recheck": "Tekrar kontrol et",
        "missing_tools": "Bulunamayan isteğe bağlı araçlar: {names}. Bazı biçimler (video, "
                         "bazı görseller) bunlar olmadan temizlenemez.",
        "sec_files": "DOSYALAR",
        "btn_clear": "Listeyi temizle",
        "empty_title": "Henüz bir şey yok",
        "empty_body": "Ne açığa çıkardığını görmek için bir fotoğraf, PDF ya da ofis belgesi "
                      "ekleyin.",
        "pill_scanning": "OKUNUYOR",
        "pill_fields": "{n} ALAN",
        "pill_one_field": "1 ALAN",
        "pill_none": "METADATA YOK",
        "pill_unsupported": "DESTEKLENMİYOR",
        "pill_error": "HATA",
        "pill_cleaned": "TEMİZLENDİ",
        "pill_failed": "BAŞARISIZ",
        "tip_remove": "Listeden çıkar",
        "btn_details": "Alanları göster",
        "btn_hide": "Alanları gizle",
        "reveals": "Ele veriyor: {cats}",
        "found_before": "ORİJİNALDE",
        "cat_location": "Konum",
        "cat_person": "Kişisel",
        "cat_device": "Cihaz ve kimlikler",
        "cat_time": "Tarihler",
        "cat_software": "Yazılım",
        "unsupported_body": "mat2 bu tür bir dosyayı okuyamıyor, bu yüzden Scrub ona dokunmuyor.",
        "error_body": "Bu dosya okunamadı: {detail}",
        "res_copy": "Temiz kopya: {name}",
        "res_replaced": "Yerinde temizlendi. Orijinali Çöp Kutusu'nda.",
        "res_left": "Temizlikten sonra hâlâ içinde: {keys}",
        "res_none_left": "Hiçbir şey kalmadı — mat2 temiz dosyada metadata bulamadı.",
        "res_rewritten": "Temizleyicinin yazdığı yeni değerler, dosyanızdan alınmadı: {keys}",
        "e_readonly_dir": "Bu klasör salt okunur, bu yüzden temiz dosya yazılamadı.",
        "e_clean_failed": "mat2 bu dosyayı temizleyemedi. Dosya değiştirilmedi.",
        "e_trash_failed": "Orijinal Çöp Kutusu'na taşınamadı, bu yüzden korundu. Temiz dosya "
                          "yanında: {name}",
        "e_changed": "Dosya temizlenirken değişti, bu yüzden yerine konmadı. Temiz sürüm "
                     "yanında: {name}",
        "e_rename_failed": "Orijinal Çöp Kutusu'nda, ama temiz dosya onun yerine konamadı. "
                           "Burada: {name}",
        "mode_copy": "Her dosyanın yanına temiz bir kopya kaydet",
        "mode_copy_hint": "Orijinalleriniz olduğu gibi kalır.",
        "mode_replace": "Orijinallerin yerine koy",
        "mode_replace_hint": "Her orijinal Çöp Kutusu'na gider, yani yine de geri alabilirsiniz.",
        "lightweight": "Hafif mod",
        "lightweight_hint": "PDF'lerde arama yapılabilmesini ve görsellerin olduğu gibi "
                            "kalmasını sağlar, ama daha az şey siler. Gerekmedikçe kapalı "
                            "bırakın.",
        "btn_clean": "{n} dosyayı temizle",
        "btn_clean_one": "1 dosyayı temizle",
        "btn_clean_none": "Temizle",
        "cleaning": "{total} dosyadan {done} tanesi temizleniyor…",
        "reading": "{total} dosyadan {done} tanesi okunuyor…",
        "sec_limits": "SCRUB'IN YAPAMADIKLARI",
        "limit_content": "Dosyanın içindekini değil, dosya hakkındaki veriyi siler: yüzler, "
                         "bir sokak tabelası, bir yansıma ya da belgede yazan bir ad "
                         "görünür kalır.",
        "limit_pdf": "Her şeyi silmek için PDF'ler görüntü olarak yeniden çizilir; metinleri "
                     "artık seçilemez ve aranamaz (Hafif mod açık değilse).",
        "limit_images": "Görseller yeniden kaydedilir; bu, JPEG kalitesini biraz "
                        "değiştirebilir.",
        "limit_identity": "Dosyalarınız sizi başka yollarla da ele verebilir: yazım tarzı, "
                          "kamera sensörü gürültüsü ya da onları gönderdiğiniz hesap.",
        "status_ready": "Listede {n} dosya",
        "status_empty": "Başlamak için dosya ekleyin",
        "status_done": "{ok} temizlendi, {failed} başarısız",
        "status_truncated": "{n} dosyada durdu — bir seferde daha az ekleyin.",
        "confirm_replace_title": "Orijinallerin yerine konsun mu?",
        "confirm_replace_body": "{n} dosya yerinde temizlenecek. Orijinaller Çöp Kutusu'na "
                                "gider; boşaltana kadar oradan geri alabilirsiniz.",
        "btn_replace": "Yerine koy",
        "btn_cancel": "Vazgeç",
        "e_helper": "mat2 başarısız oldu: {detail}",
        "e_cancelled": "Bu dosya temizlenmeden durduruldu. Dosya değiştirilmedi.",
        "btn_stop": "Durdur",
        "status_stopped": "Durduruldu. Bitmiş dosyaların sonuçları korunuyor.",
        "pill_stopped": "OKUNMADI",
        "not_read": "Okunmadı — Scrub bu dosyaya gelmeden durdurdunuz. Okumak için yeniden ekleyin.",
        "e_timeout": "mat2 çok uzun sürdü ve durduruldu.",
    },
}

_lang = "en"


def set_language(lang: str) -> None:
    global _lang
    _lang = lang if lang in STRINGS else "en"


def language() -> str:
    return _lang


def t(key: str, /, **kw) -> str:
    text = STRINGS.get(_lang, {}).get(key) or STRINGS["en"].get(key) or key
    return text.format(**kw) if kw else text
