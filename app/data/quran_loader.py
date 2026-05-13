# app/data/quran_loader.py

import json
import requests
from typing import List
from app.core.models import Ayah, Word
from app.nlp.normalizer import normalize_arabic

QURAN_API_BASE = "https://api.quran.com/api/v4"


def load_surah(file_path: str) -> List[Ayah]:
    """Load a surah from a local JSON file."""
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return _parse_ayahs(data["ayahs"])


def fetch_surah_from_api(surah_number: int) -> List[Ayah]:
    """
    Fetch a complete surah from Quran.com API (v4).
    Uses Uthmani script (full harakat) — best for normalizer to process.
    """
    url = f"{QURAN_API_BASE}/quran/verses/uthmani"
    params = {"chapter_number": surah_number}

    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
    except requests.RequestException as e:
        raise RuntimeError(
            f"Failed to fetch surah {surah_number} from Quran.com API: {e}\n"
            "Ensure internet access or use a local JSON file with load_surah()."
        )

    verses = resp.json().get("verses", [])
    raw_ayahs = [
        {"ayah_number": v["verse_number"], "text": v["text_uthmani"]}
        for v in verses
    ]
    return _parse_ayahs(raw_ayahs)


def _parse_ayahs(raw_ayahs: list) -> List[Ayah]:
    """
    Parse raw ayah data (either from local JSON or API).
    
    Expected format:
    [
        {"ayah_number": 1, "text": "بِسْمِ اللَّهِ..."},
        ...
    ]
    """
    ayahs = []
    for ayah_data in raw_ayahs:
        # ✅ Handle missing text field
        text = ayah_data.get("text", "")
        if not text:
            raise ValueError(f"Missing 'text' field in ayah: {ayah_data}")
        
        # ✅ Handle missing ayah_number field
        ayah_number = ayah_data.get("ayah_number")
        if ayah_number is None:
            raise ValueError(f"Missing 'ayah_number' field in ayah: {ayah_data}")
        
        # ✅ Split text into words
        words_raw = text.split()
        words = [
            Word(text=w, normalized=normalize_arabic(w))
            for w in words_raw
            if w.strip()
        ]
        
        ayahs.append(Ayah(
            ayah_number=ayah_number,
            words=words,
        ))
    return ayahs


def save_surah_to_json(surah_number: int, output_path: str):
    """
    Fetch a surah from Quran.com API and cache it locally.
    Run once per surah for offline use.

    Example:
        from app.data.quran_loader import save_surah_to_json
        save_surah_to_json(1, "data/al_fatiha.json")
        save_surah_to_json(112, "data/al_ikhlas.json")
    """
    ayahs = fetch_surah_from_api(surah_number)
    output = {
        "surah": surah_number,
        "ayahs": [
            {"ayah_number": a.ayah_number, "text": " ".join(w.text for w in a.words)}
            for a in ayahs
        ]
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"✅ Saved surah {surah_number} → {output_path}")
