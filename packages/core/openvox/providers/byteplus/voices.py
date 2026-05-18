"""BytePlus TTS 2.0 voice catalogue.

Source of truth: BytePlus public voice list. Mirrored from
`ModelMD/TTS2_voices.md` on 2026-05-19 — when the catalogue grows on
the provider side, refresh this file and bump VOICES_REFRESHED_AT.

Why this is a static module rather than a live probe:
    Probing each voice with a real TTS call would be slow (40+
    voices × ~200ms each) and burn API quota every time the
    dashboard renders the agent-edit form. The catalogue is stable
    enough — voices get added monthly, not by the hour — that a
    static list refreshed by hand is fine. Pair this with a
    per-voice "test voice" button in the UI when users want to
    actually hear a sample.

The `language` field uses BytePlus's free-form description ("Mixed
English & Chinese, Japanese, Mexican Spanish, Indonesian Bahasa") so
we keep their exact wording. The `language_codes` field is our
normalized list of BCP-47-ish hints derived from `language` so the
dashboard can filter ("show me Spanish voices").
"""

from __future__ import annotations

from dataclasses import dataclass, asdict


VOICES_REFRESHED_AT = "2026-05-19"


@dataclass(frozen=True)
class Voice:
    id: str
    name: str
    language: str
    gender: str
    style: str
    scenario: str = "General"
    language_codes: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return asdict(self)


# Order matches the BytePlus docs page so it's easy to diff against
# the source when refreshing.
VOICES: list[Voice] = [
    Voice("zh_female_vv_uranus_bigtts", "Vivi", "Mixed English & Chinese, Japanese, Mexican Spanish, Indonesian Bahasa, Chinese", "Female", "Vivid", language_codes=("en", "zh", "ja", "es", "id")),
    Voice("zh_female_xiaohe_uranus_bigtts", "Mindy", "English, Mexican Spanish, Indonesian Bahasa, Brazilian Portuguese, Chinese", "Female", "Vivid", language_codes=("en", "zh", "es", "id", "pt")),
    Voice("en_female_stokie_uranus_bigtts", "Stokie", "English", "Female", "Clear", language_codes=("en",)),
    Voice("en_female_dacey_uranus_bigtts", "Dacey", "English", "Female", "Sweet", language_codes=("en",)),
    Voice("en_male_tim_uranus_bigtts", "Tim", "English", "Male", "Clear", language_codes=("en",)),
    Voice("zh_male_m191_uranus_bigtts", "Kian", "English, Chinese", "Male", "Clear", language_codes=("en", "zh")),
    Voice("zh_male_taocheng_uranus_bigtts", "Cedric", "English, Chinese", "Male", "Clear", language_codes=("en", "zh")),
    Voice("zh_male_sophie_uranus_bigtts", "Sophie", "English, Chinese", "Female", "Clear", language_codes=("en", "zh")),
    Voice("zh_female_yingyujiaoxue_uranus_bigtts", "Jean", "English, Chinese", "Female", "Warm", language_codes=("en", "zh")),
    Voice("zh_male_dayi_uranus_bigtts", "Magnus", "English, Chinese", "Male", "Clear", language_codes=("en", "zh")),
    Voice("zh_female_mizai_uranus_bigtts", "Mabel", "English, Chinese", "Female", "Sweet", language_codes=("en", "zh")),
    Voice("zh_female_jitangnv_uranus_bigtts", "Nadia", "English, Chinese", "Female", "Warm", language_codes=("en", "zh")),
    Voice("zh_female_meilinvyou_uranus_bigtts", "Opal", "English, Chinese", "Female", "Charming", language_codes=("en", "zh")),
    Voice("zh_female_liuchangnv_uranus_bigtts", "Pearl", "English, Chinese", "Female", "Clear", language_codes=("en", "zh")),
    Voice("zh_male_ruyayichen_uranus_bigtts", "Quentin", "English, Chinese", "Male", "Warm", language_codes=("en", "zh")),
    Voice("zh_female_vivo_uranus_bigtts", "Vienna", "Mixed English & Chinese", "Female", "Clear", language_codes=("en", "zh")),
    Voice("zh_female_xiaoai_uranus_bigtts", "Alina", "Mixed English & Chinese", "Female", "Clear", language_codes=("en", "zh")),
    Voice("zh_female_cancan_uranus_bigtts", "Corinne", "Mixed English & Chinese", "Female", "Vivid", language_codes=("en", "zh")),
    Voice("zh_female_tianmeixiaoyuan_uranus_bigtts", "Esther", "Mixed English & Chinese", "Female", "Sweet", language_codes=("en", "zh")),
    Voice("zh_female_tianmeitaozi_uranus_bigtts", "Freya", "Mixed English & Chinese", "Female", "Sweet", language_codes=("en", "zh")),
    Voice("zh_female_shuangkuaisisi_uranus_bigtts", "Gigi", "Mixed English & Chinese", "Female", "Vivid", language_codes=("en", "zh")),
    Voice("zh_female_peiqi_uranus_bigtts", "Holly", "Mixed English & Chinese", "Female", "Cute", language_codes=("en", "zh")),
    Voice("zh_female_xiaoxue_uranus_bigtts", "Lyla", "Mixed English & Chinese", "Female", "Warm", language_codes=("en", "zh")),
    Voice("zh_female_yuanqi_uranus_bigtts", "Daisy", "Mixed English & Chinese", "Female", "Vivid", language_codes=("en", "zh")),
    Voice("zh_female_kefunvsheng_uranus_bigtts", "Tracy", "Mexican Spanish, Chinese", "Female", "Warm", language_codes=("es", "zh")),
    Voice("zh_male_shaonianzixin_uranus_bigtts", "Jess", "Japanese, Mexican Spanish, Indonesian Bahasa, Brazilian Portuguese, English, Chinese", "Male", "Vivid", language_codes=("ja", "es", "id", "pt", "en", "zh")),
    Voice("zh_female_linjianvhai_uranus_bigtts", "Pinky", "Mexican Spanish, Korean, Mixed English & Chinese", "Female", "Sweet", language_codes=("es", "ko", "en", "zh")),
    Voice("zh_female_kiwi_uranus_bigtts", "Sweety", "Japanese, Mexican Spanish", "Female", "Vivid", language_codes=("ja", "es")),
    Voice("zh_female_sajiaoxuemei_uranus_bigtts", "Sandy", "Mexican Spanish, Mixed English & Chinese", "Female", "Sweet", language_codes=("es", "en", "zh")),
    Voice("de_male_seven_uranus_bigtts", "Sven", "German", "Male", "Clear", language_codes=("de",)),
    Voice("jp_female_minimi_uranus_bigtts", "Minimi", "Japanese", "Female", "Clear", language_codes=("ja",)),
    Voice("fr_male_usseau_uranus_bigtts", "Usseau", "French", "Male", "Clear", language_codes=("fr",)),
    Voice("es_male_felipe_uranus_bigtts", "Felipe", "Mexican Spanish", "Male", "Clear", language_codes=("es",)),
    Voice("id_male_han_uranus_bigtts", "Han", "Indonesian Bahasa", "Male", "Clear", language_codes=("id",)),
    Voice("pt_male_martins_uranus_bigtts", "Martins", "Brazilian Portuguese", "Male", "Clear", language_codes=("pt",)),
    Voice("it_male_enzo_uranus_bigtts", "Enzo", "Italian", "Male", "Clear", language_codes=("it",)),
    Voice("kr_male_shane_uranus_bigtts", "지훈", "Korean", "Male", "Clear", language_codes=("ko",)),
    Voice("zh_female_dabing_uranus_bigtts", "Bonnie", "Chinese", "Female", "Clear", language_codes=("zh",)),
    Voice("zh_male_liufei_uranus_bigtts", "Felix", "Chinese", "Male", "Clear", language_codes=("zh",)),
    Voice("zh_female_qingxinnvsheng_uranus_bigtts", "Celeste", "Chinese", "Female", "Clear", language_codes=("zh",)),
    Voice("zh_male_sunwukong_uranus_bigtts", "Monkey King", "Chinese", "Male", "Clear", language_codes=("zh",)),
]


# Fast id→Voice lookup for validation / dashboard rendering.
VOICES_BY_ID: dict[str, Voice] = {v.id: v for v in VOICES}


def is_known(voice_id: str) -> bool:
    """Return True when `voice_id` exists in the TTS 2.0 catalogue."""
    return voice_id in VOICES_BY_ID


def voices_for_language(code: str) -> list[Voice]:
    """All catalogue entries that support the given BCP-47-ish short code.

    Used by multilingual templates to pick a real voice for a language
    instead of fabricating an ID. Returns [] when the catalogue has no
    voice covering that language (Hindi, Cantonese, etc.) — caller
    should fall back gracefully or document the gap.
    """
    return [v for v in VOICES if code in v.language_codes]
