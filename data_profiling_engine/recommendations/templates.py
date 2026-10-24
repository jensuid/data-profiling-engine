"""Recommendation templates — the closed rule-based catalog (M2-T9, ENGINE §7).

MVP recommendations are **rule-based / template with evidence substitution**
(``recommendations/rule_based.py`` executes them; AI/LLM is out of MVP,
BLUEPRINT Q3). Every :class:`Finding` produced by M2-T8 is classified into one
*problem kind* — the detector that surfaced it, recovered from the finding's
``evidence_refs`` (a ``rule:`` ref ⇒ a quality-rule finding keyed by its
dimension) or its representative fact ``metric_ref`` suffix (a rule-less profile
extreme). Each kind maps to exactly one template that lists:

* ``action``       — imperative next step (``{column}`` substituted),
* ``rationale``    — why, grounded in the cited evidence (``{column}`` subs.),
* ``target_improvement`` — observable state after the action lands,
* ``do_not``       — anti-pattern guardrails (e.g. "don't zero-fill"),
* ``probe``        — an investigation directive (e.g. "group missing rate by
  status / source / period"), folded into the action sentence,
* ``reason_type``  — ∈ {evidence, business_context_required} per CONTRACTS §4.3.

**No fabrication (AGENTS §3, CONTRACTS §7).** A template never carries a number
or a cause: numbers ride the finding's own evidence refs (substituted verbatim
downstream in :mod:`rule_based`), and a *review signal* (accuracy / dominant
category — "accuracy is never inferred from validity", ENGINE §5) is phrased as
an investigation question with ``reason_type=business_context_required``, never
a proven defect.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from data_profiling_engine.schemas.models import (
    Finding,
    ReasonType,
    ReportLanguage,
)

# ---------------------------------------------------------------------------
# Narrative language axis (ADR-018 / BLUEPRINT Q1).
#
# Metric *labels* stay English; only the recommendation narrative strings are
# bilingual. ``en`` is the shipped default so golden / existing output is byte
# identical; ``id`` (Indonesian) is opt-in via ``DatasetConfig.language``. Both
# catalogs share the SAME kind tokens and per-kind ``reason_type`` — the
# Recommendation *contract shape* (§4.3) never changes, only the wording does.
# ---------------------------------------------------------------------------

DEFAULT_LANGUAGE = ReportLanguage.EN.value
SUPPORTED_LANGUAGES: Tuple[str, ...] = tuple(lang.value for lang in ReportLanguage)


# ---------------------------------------------------------------------------
# Kind identity tokens.
#
# A rule-derived finding keys on its dimension (finding.category, shared with
# QualityCheckResult.dimension, CONTRACTS §4.1); a profile-extreme finding keys
# on the *measure* its representative fact was read from (the detector signal
# M2-T8 left in Finding.facts[0].metric_ref). The tokens below are the closed
# set TEMPLATES covers; classify_finding_kind() always returns one of them.
# ---------------------------------------------------------------------------

RULE_KIND_PREFIX = "rule"
_PROFILE_KIND_PREFIX = "profile"

# Profile detectors (findings/detectors.py §6.1) identified by the suffix of
# their fact metric_ref — matched in order, most specific first.
_PROFILE_SUFFIX_KINDS: Tuple[Tuple[str, str], ...] = (
    (".missingness.missing_rate", f"{_PROFILE_KIND_PREFIX}:completeness:missingness"),
    (".uniqueness.duplicate_group_count", f"{_PROFILE_KIND_PREFIX}:uniqueness:duplicate_key"),
    (".uniqueness.distinct_count", f"{_PROFILE_KIND_PREFIX}:uniqueness:constant_column"),
    (".categorical.frequencies", f"{_PROFILE_KIND_PREFIX}:accuracy_flagged:dominant_category"),
)

# Every category that classify_finding_kind can emit a rule/generic kind for
# (QualityDimension, CONTRACTS §4.1).
_CATEGORIES: Tuple[str, ...] = (
    "completeness",
    "uniqueness",
    "validity",
    "consistency",
    "timeliness",
    "accuracy_flagged",
)


def rule_kind(category: str) -> str:
    return f"{RULE_KIND_PREFIX}:{category}"


def generic_profile_kind(category: str) -> str:
    """Defensive fallback for a profile extreme whose measure suffix is unknown
    (should never fire for MVP detectors — all emit a mapped suffix)."""
    return f"{_PROFILE_KIND_PREFIX}:{category}:generic"


@dataclass(frozen=True)
class RecommendationTemplate:
    """One rule-based recommendation blueprint (ENGINE §7). ``{column}``
    placeholders are substituted by :mod:`recommendations.narrative`."""

    kind: str
    reason_type: ReasonType
    action: str
    rationale: str
    target_improvement: str
    do_not: Tuple[str, ...]
    probe: str


# ---------------------------------------------------------------------------
# The catalog. Keyed by kind; consumed by rule_based.recommendations_for_finding.
# ``reason_type`` policy: a concrete measured breach ⇒ EVIDENCE; a *review
# signal* whose cause needs business context (accuracy-flagged / dominant
# category) ⇒ BUSINESS_CONTEXT_REQUIRED, phrased as an investigation question
# and never asserting a proven cause (CONTRACTS §4.3).
# ---------------------------------------------------------------------------


def _t(kind: str, reason: ReasonType, action: str, rationale: str, target: str,
       do_not: Tuple[str, ...], probe: str) -> RecommendationTemplate:
    return RecommendationTemplate(
        kind=kind, reason_type=reason, action=action, rationale=rationale,
        target_improvement=target, do_not=do_not, probe=probe,
    )


_EV = ReasonType.EVIDENCE
_BC = ReasonType.BUSINESS_CONTEXT_REQUIRED

# -- Rule-derived findings (one per quality dimension) ----------------------
_RULE_TEMPLATES: Tuple[RecommendationTemplate, ...] = (
    _t(
        rule_kind("completeness"), _EV,
        "Trace the completeness gap on {column} back to its source system and sync mechanism, then backfill the field or document why the values are legitimately absent.",
        "A quality rule on {column} failed its completeness bound, so counts and aggregates that rely on this field under-cover the real records.",
        "Unexplained missingness on {column} sits below the configured threshold, or every remaining gap has a documented cause.",
        (
            "Do not fill empty values with zero as a first response.",
            "Do not drop rows only to make the field appear complete.",
        ),
        "Group the missing rate on {column} by status / source / period to localise where the gap is introduced.",
    ),
    _t(
        rule_kind("uniqueness"), _EV,
        "Find the upstream process that lets {column} repeat and enforce the key constraint where the records are created.",
        "A uniqueness rule on {column} was violated, so joins or aggregations keyed on this field can double-count records.",
        "{column} satisfies its uniqueness rule, or the repetitions are confirmed as legitimate and the key expectation is corrected.",
        (
            "Do not delete duplicate rows before confirming they are not distinct real records.",
            "Do not relax the uniqueness rule just to silence the finding.",
        ),
        "Inspect the duplicated {column} values and trace them to their source rows.",
    ),
    _t(
        rule_kind("validity"), _EV,
        "Compare the offending {column} values against the intended domain/format and fix them at the source or the mapping layer.",
        "Values in {column} fall outside the configured validity rule, so downstream logic that assumes the domain can fail or mis-bucket them.",
        "{column} conforms to the declared domain/format, or the domain definition is corrected to match the real data.",
        (
            "Do not coerce invalid values into the expected type just to pass the rule.",
            "Do not widen the allowed domain before the offending values are understood.",
        ),
        "Sample the invalid {column} rows and compare them against the intended domain.",
    ),
    _t(
        rule_kind("consistency"), _EV,
        "Reconcile {column} with the paired field it disagrees against and fix the process or mapping that lets the two diverge.",
        "A cross-field consistency rule involving {column} failed, so the paired fields can describe conflicting states for the same record.",
        "The paired fields agree on every record, or a documented exception states where they may legitimately differ.",
        (
            "Do not overwrite one field from the other before confirming which side is authoritative.",
        ),
        "Compare the paired fields across the violating {column} rows to see which side drifts.",
    ),
    _t(
        rule_kind("timeliness"), _EV,
        "Check the ingestion/update cadence behind {column} and close the lag or missing period that made the data stale.",
        "A temporal/timeliness rule on {column} failed, so period-based analysis over this field may omit or misplace time windows.",
        "{column} covers the expected periods with no unexplained gaps or stale windows.",
        (
            "Do not interpolate missing periods to make a time series look continuous.",
        ),
        "Chart volume over time for {column} to locate the gap or stale window.",
    ),
    _t(
        rule_kind("accuracy_flagged"), _BC,
        "Confirm the flagged {column} pattern against the business definition before treating it as an error.",
        "The {column} accuracy/outlier signal is a review flag, not a proven defect — accuracy is never inferred from validity alone (ENGINE §5).",
        "A business owner confirms the flagged {column} pattern is expected, or points to the records that need correction.",
        (
            "Do not auto-correct or clip flagged values as a first response.",
            "Do not read a passing validity check as proof the values are business-correct.",
        ),
        "Compare the flagged {column} values against an external source of truth or the business definition.",
    ),
)

# -- Rule-less profile extremes (the four §6.1 profile detectors) -----------
_PROFILE_TEMPLATES: Tuple[RecommendationTemplate, ...] = (
    _t(
        f"{_PROFILE_KIND_PREFIX}:completeness:missingness", _EV,
        "Investigate why {column} is almost entirely absent and whether the source still produces the field.",
        "Near-total missingness was measured on {column} with no rule configured, so any analysis relying on this field is effectively uncovered.",
        "{column} is populated wherever the source emits it, or the field is excluded from downstream use with a documented reason.",
        (
            "Do not impute or default the values — a near-empty column carries no real signal to impute from.",
        ),
        "Group the missing rate on {column} by source / period to see whether entire loads are absent.",
    ),
    _t(
        f"{_PROFILE_KIND_PREFIX}:uniqueness:duplicate_key", _EV,
        "Verify whether {column} is intended to be a unique identifier; if so, trace the duplicated key values to their source.",
        "Duplicated key values were measured on {column} with no rule configured, so it may not be a reliable join key at the intended grain.",
        "{column} is confirmed unique at its grain, or documented as non-unique so joins and aggregations stop treating it as a key.",
        (
            "Do not assume the duplicates are errors — they may reflect the true record grain.",
        ),
        "Inspect the duplicated {column} values and compare them against the dataset's declared grain.",
    ),
    _t(
        f"{_PROFILE_KIND_PREFIX}:uniqueness:constant_column", _EV,
        "Confirm whether {column} is expected to be constant; if not, trace the upstream value or mapping that flattened it.",
        "{column} holds a single distinct value across the population, so it carries no discriminating information for grouping or keying.",
        "{column} varies as the business expects, or it is documented as a constant and excluded from any discriminating role.",
        (
            "Do not silently drop the column before confirming that a constant value is not itself meaningful.",
        ),
        "Check whether {column} is constant at the source or only became constant through a transformation.",
    ),
    _t(
        f"{_PROFILE_KIND_PREFIX}:accuracy_flagged:dominant_category", _BC,
        "Confirm whether the near-single-category distribution in {column} is expected for this dataset.",
        "Almost all non-null values of {column} fall in one category; this is a review signal (accuracy is never inferred from validity), not a proven defect.",
        "A business owner confirms the dominant {column} distribution is expected, or identifies the mapping that collapsed it.",
        (
            "Do not rebalance or re-label categories just to force the distribution to spread.",
        ),
        "Check whether {column} collapsed to one category at the source or through a filtering/mapping step.",
    ),
)

# -- Defensive generic fallbacks (only if a fact has no mapped suffix) ------
_GENERIC_TEMPLATES: Tuple[RecommendationTemplate, ...] = tuple(
    _t(
        generic_profile_kind(cat), _EV if cat != "accuracy_flagged" else _BC,
        f"Investigate the flagged {cat} signal on {{column}} against the source and the business definition.",
        f"A {cat} profile extreme was observed on {{column}} with no rule configured, so it is surfaced for review.",
        f"The {cat} pattern on {{column}} is understood and either resolved or documented.",
        (f"Do not auto-remediate the {cat} signal without confirming intent.",),
        f"Group or sample {cat}-relevant values on {{column}} to confirm the pattern.",
    )
    for cat in _CATEGORIES
)


# ---------------------------------------------------------------------------
# Indonesian narrative catalog (ADR-018 / BLUEPRINT Q1). Same kind tokens and
# same per-kind ``reason_type`` as the English catalog above — a review signal
# stays ``business_context_required`` regardless of language, and a concrete
# measured breach stays ``evidence``. Only the human-readable strings are
# translated; the ``{column}`` slot and the no-fabrication law (numbers live in
# the cited evidence, not the template) are preserved verbatim. The ``cat``
# quality-dimension token in the generic fallbacks is a technical identifier and
# is intentionally left English in both languages.
# ---------------------------------------------------------------------------

_RULE_TEMPLATES_ID: Tuple[RecommendationTemplate, ...] = (
    _t(
        rule_kind("completeness"), _EV,
        "Telusuri celah kelengkapan pada {column} kembali ke sistem sumber dan mekanisme sinkronisasinya, lalu isi kembali nilai yang hilang atau dokumentasikan mengapa nilai itu memang sah tidak ada.",
        "Sebuah aturan quality pada {column} melewati batas kelengkapan yang dikonfigurasi, sehingga hitungan dan agregasi yang bergantung pada kolom ini tidak mencakup seluruh catatan nyata.",
        "Kelengkapan yang tidak terjelaskan pada {column} berada di bawah ambang yang dikonfigurasi, atau setiap celah yang tersisa memiliki penyebab yang terdokumentasi.",
        (
            "Jangan mengisi nilai kosong dengan nol sebagai respons pertama.",
            "Jangan menghapus baris hanya agar kolom tampak lengkap.",
        ),
        "Kelompokkan tingkat missing pada {column} berdasarkan status / sumber / periode untuk melokalisasi di mana celah itu muncul.",
    ),
    _t(
        rule_kind("uniqueness"), _EV,
        "Temukan proses hulu yang membuat {column} berulang dan tegakkan batasan key di tempat catatan dibuat.",
        "Aturan keunikan pada {column} dilanggar, sehingga join atau agregasi yang berkey pada kolom ini dapat menghitung ganda catatan.",
        "{column} memenuhi aturan keunikannya, atau pengulangan itu terkonfirmasi sah dan ekspektasi key-nya dikoreksi.",
        (
            "Jangan menghapus baris duplikat sebelum memastikan baris itu bukan catatan nyata yang berbeda.",
            "Jangan melonggarkan aturan keunikan hanya untuk membungkam temuan.",
        ),
        "Periksa nilai {column} yang terduplikasi dan telusuri ke baris sumbernya.",
    ),
    _t(
        rule_kind("validity"), _EV,
        "Bandingkan nilai {column} yang menyimpang dengan domain/format yang diharapkan dan perbaiki di sumbernya atau di lapisan mapping.",
        "Nilai pada {column} berada di luar aturan validitas yang dikonfigurasi, sehingga logika hilir yang mengasumsikan domain itu dapat gagal atau salah mengelompokkannya.",
        "{column} sesuai dengan domain/format yang dinyatakan, atau definisi domain dikoreksi agar cocok dengan data nyata.",
        (
            "Jangan memaksa nilai tidak valid ke tipe yang diharapkan hanya agar lolos aturan.",
            "Jangan memperluas domain yang diizinkan sebelum nilai yang menyimpang dipahami.",
        ),
        "Ambil sampel baris {column} yang tidak valid dan bandingkan dengan domain yang diharapkan.",
    ),
    _t(
        rule_kind("consistency"), _EV,
        "Rekonsiliasi {column} dengan kolom berpasangan yang bertentangan dengannya, lalu perbaiki proses atau mapping yang membuat keduanya menyimpang.",
        "Aturan konsistensi lintas-kolom yang melibatkan {column} gagal, sehingga kolom-kolom berpasangan dapat menyatakan status yang bertentangan untuk catatan yang sama.",
        "Kolom-kolom berpasangan sepakat pada setiap catatan, atau sebuah pengecualian terdokumentasi menyatakan di mana keduanya boleh berbeda secara sah.",
        (
            "Jangan menimpa satu kolom dari kolom lain sebelum memastikan sisi mana yang otoritatif.",
        ),
        "Bandingkan kolom-kolom berpasangan di seluruh baris {column} yang melanggar untuk melihat sisi mana yang menyimpang.",
    ),
    _t(
        rule_kind("timeliness"), _EV,
        "Periksa ritme ingestion/pembaruan di balik {column} dan tutup keterlambatan atau periode hilang yang membuat data menjadi basi.",
        "Aturan temporal/timeliness pada {column} gagal, sehingga analisis berbasis periode pada kolom ini dapat melewatkan atau salah menempatkan jendela waktu.",
        "{column} mencakup periode yang diharapkan tanpa celah yang tidak terjelaskan atau jendela yang basi.",
        (
            "Jangan menginterpolasi periode yang hilang agar deret waktu tampak berlanjut.",
        ),
        "Buat grafik volume terhadap waktu untuk {column} guna menemukan celah atau jendela basi.",
    ),
    _t(
        rule_kind("accuracy_flagged"), _BC,
        "Konfirmasi pola {column} yang ditandai terhadap definisi bisnis sebelum memperlakukannya sebagai kesalahan.",
        "Sinyal accuracy/outlier pada {column} adalah tanda untuk ditinjau, bukan cacat yang terbukti — accuracy tidak pernah disimpulkan dari validity saja (ENGINE §5).",
        "Pemilik bisnis mengonfirmasi pola {column} yang ditandai itu diharapkan, atau menunjuk catatan yang perlu dikoreksi.",
        (
            "Jangan otomatis mengoreksi atau memotong nilai yang ditandai sebagai respons pertama.",
            "Jangan membaca check validity yang lulus sebagai bukti bahwa nilai itu benar secara bisnis.",
        ),
        "Bandingkan nilai {column} yang ditandai terhadap sumber kebenaran eksternal atau definisi bisnis.",
    ),
)

_PROFILE_TEMPLATES_ID: Tuple[RecommendationTemplate, ...] = (
    _t(
        f"{_PROFILE_KIND_PREFIX}:completeness:missingness", _EV,
        "Selidiki mengapa {column} hampir sepenuhnya kosong dan apakah sumber masih menghasilkan kolom tersebut.",
        "Kelengkapan nyaris nol terukur pada {column} tanpa aturan yang dikonfigurasi, sehingga setiap analisis yang bergantung pada kolom ini praktis tidak tercakup.",
        "{column} terisi setiap kali sumber memancarkannya, atau kolom dikeluarkan dari penggunaan hilir dengan alasan terdokumentasi.",
        (
            "Jangan mengimputasi atau memberi nilai default — kolom yang hampir kosong tidak membawa sinyal nyata untuk diimputasi.",
        ),
        "Kelompokkan tingkat missing pada {column} berdasarkan sumber / periode untuk melihat apakah seluruh muatan tidak ada.",
    ),
    _t(
        f"{_PROFILE_KIND_PREFIX}:uniqueness:duplicate_key", _EV,
        "Verifikasi apakah {column} dimaksudkan sebagai identifier unik; jika ya, telusuri nilai key yang terduplikasi ke sumbernya.",
        "Nilai key yang terduplikasi terukur pada {column} tanpa aturan yang dikonfigurasi, sehingga kolom ini mungkin bukan key join yang andal pada grain yang diharapkan.",
        "{column} terkonfirmasi unik pada grain-nya, atau didokumentasikan sebagai tidak unik sehingga join dan agregasi berhenti memperlakukannya sebagai key.",
        (
            "Jangan mengasumsikan duplikat sebagai kesalahan — bisa jadi mencerminkan grain catatan yang sebenarnya.",
        ),
        "Periksa nilai {column} yang terduplikasi dan bandingkan dengan grain yang dideklarasikan dataset.",
    ),
    _t(
        f"{_PROFILE_KIND_PREFIX}:uniqueness:constant_column", _EV,
        "Konfirmasi apakah {column} diharapkan bernilai konstan; jika tidak, telusuri nilai atau mapping hulu yang mematakannya.",
        "{column} menyimpan satu nilai unik di seluruh populasi, sehingga tidak membawa informasi pembeda untuk pengelompokan maupun keying.",
        "{column} bervariasi sebagaimana diharapkan bisnis, atau didokumentasikan sebagai konstan dan dikeluarkan dari peran pembeda apa pun.",
        (
            "Jangan diam-diam menghapus kolom sebelum memastikan bahwa nilai konstan bukan berarti tidak bermakna.",
        ),
        "Periksa apakah {column} konstan di sumber atau baru menjadi konstan melalui suatu transformasi.",
    ),
    _t(
        f"{_PROFILE_KIND_PREFIX}:accuracy_flagged:dominant_category", _BC,
        "Konfirmasi apakah distribusi hampir satu-kategori pada {column} diharapkan untuk dataset ini.",
        "Hampir seluruh nilai non-null {column} jatuh pada satu kategori; ini adalah sinyal untuk ditinjau (accuracy tidak pernah disimpulkan dari validity), bukan cacat yang terbukti.",
        "Pemilik bisnis mengonfirmasi distribusi dominan {column} itu diharapkan, atau mengidentifikasi mapping yang merapatkannya.",
        (
            "Jangan menyeimbangkan ulang atau memberi label ulang kategori hanya agar distribusinya tersebar.",
        ),
        "Periksa apakah {column} merapat ke satu kategori di sumber atau melalui langkah filtering/mapping.",
    ),
)

_GENERIC_TEMPLATES_ID: Tuple[RecommendationTemplate, ...] = tuple(
    _t(
        generic_profile_kind(cat), _EV if cat != "accuracy_flagged" else _BC,
        f"Selidiki sinyal {cat} yang ditandai pada {{column}} terhadap sumber dan definisi bisnis.",
        f"Ekstrem profil {cat} teramati pada {{column}} tanpa aturan yang dikonfigurasi, sehingga ditampilkan untuk ditinjau.",
        f"Pola {cat} pada {{column}} dipahami dan kemudian diselesaikan atau didokumentasikan.",
        (f"Jangan melakukan auto-remediasi terhadap sinyal {cat} tanpa mengonfirmasi maksudnya.",),
        f"Kelompokkan atau ambil sampel nilai terkait {cat} pada {{column}} untuk mengonfirmasi pola tersebut.",
    )
    for cat in _CATEGORIES
)


def _build_templates(
    templates: Tuple[RecommendationTemplate, ...],
) -> Dict[str, RecommendationTemplate]:
    catalog: Dict[str, RecommendationTemplate] = {}
    for tpl in templates:
        if tpl.kind in catalog:
            raise ValueError(f"duplicate recommendation template kind {tpl.kind!r}")
        catalog[tpl.kind] = tpl
    return catalog


# Per-language catalog, keyed by (language, kind). The kind token set is
# identical across languages, so every catalog has the same keys.
TEMPLATES_BY_LANGUAGE: Dict[str, Dict[str, RecommendationTemplate]] = {
    ReportLanguage.EN.value: _build_templates(
        (*_RULE_TEMPLATES, *_PROFILE_TEMPLATES, *_GENERIC_TEMPLATES)
    ),
    ReportLanguage.ID.value: _build_templates(
        (*_RULE_TEMPLATES_ID, *_PROFILE_TEMPLATES_ID, *_GENERIC_TEMPLATES_ID)
    ),
}

# Backward-compatible default-language view (the shipped "en" catalog).
TEMPLATES: Dict[str, RecommendationTemplate] = TEMPLATES_BY_LANGUAGE[DEFAULT_LANGUAGE]


def templates_for_language(language: Optional[str] = DEFAULT_LANGUAGE) -> Dict[str, RecommendationTemplate]:
    """Return the recommendation catalog for ``language`` ("en" | "id").

    Accepts a bare str, an enum value, a :class:`ReportLanguage` member, or
    ``None`` (the DatasetConfig field is optional and omitted-when-unset; None ==
    the shipped "en" default). An unsupported non-null language is a hard error
    rather than a silent English fallback, so a typo in config surfaces instead
    of quietly shipping the wrong narrative.
    """
    if language is None:
        return TEMPLATES_BY_LANGUAGE[DEFAULT_LANGUAGE]
    try:
        lang = ReportLanguage(language).value
    except ValueError as exc:  # not a canonical value
        raise ValueError(
            f"unsupported recommendation language {language!r}; "
            f"expected one of {SUPPORTED_LANGUAGES}"
        ) from exc
    return TEMPLATES_BY_LANGUAGE[lang]


def classify_finding_kind(finding: Finding) -> str:
    """Map a M2-T8 :class:`Finding` to the template kind of its representative
    detector. The Finding schema (§4.2) carries no ``detector_id``, so identity
    is recovered from the evidence the detector attached:

    * any ``rule:`` evidence ref ⇒ a rule-derived finding keyed by its category;
    * else the representative fact's ``metric_ref`` suffix ⇒ the profile extreme;
    * else a defensive per-category generic.
    """
    if any(ref.startswith("rule:") for ref in finding.evidence_refs):
        return rule_kind(finding.category)
    metric_ref = finding.facts[0].metric_ref if finding.facts else ""
    for suffix, kind in _PROFILE_SUFFIX_KINDS:
        if metric_ref.endswith(suffix):
            return kind
    return generic_profile_kind(finding.category)


__all__ = [
    "RecommendationTemplate",
    "TEMPLATES",
    "TEMPLATES_BY_LANGUAGE",
    "DEFAULT_LANGUAGE",
    "SUPPORTED_LANGUAGES",
    "templates_for_language",
    "classify_finding_kind",
    "rule_kind",
    "generic_profile_kind",
]
