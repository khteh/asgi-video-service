"""Offline, deterministic query validation.

No network access and no API key required, so this is always available and
always runs first - it is what protects the service from the cheap failure
cases (empty strings, symbol soup, pure digits) without spending a network
round trip, and it is the sole validator when no LLM key is configured.
"""
from __future__ import annotations

import re

from wordfreq import zipf_frequency

from src.domain.errors import InvalidQueryError, NotStemRelevantError

_WORD_RE = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")

MIN_LENGTH = 5
MAX_LENGTH = 1024
MIN_REAL_WORD_RATIO = 0.55
# zipf_frequency returns ~0 for strings that aren't recognizable English
# words at all; short common words score high (>4), rare-but-real words
# still clear ~1.5-2.5. This threshold is deliberately lenient so real STEM
# vocabulary ("covalent", "photosynthesis") isn't mistaken for gibberish.
ZIPF_KNOWN_THRESHOLD = 1.2
MIN_ALPHA_RATIO = 0.5

# A deliberately broad, curated STEM vocabulary used for the fast rule-based
# relevance heuristic. This is intentionally generous (false negatives here
# just mean the optional LLM validator - or a human - has to do the more
# nuanced semantic judgment); it does not need to be exhaustive.
STEM_KEYWORDS: set[str] = {
    # general science / meta
    "science", "scientific", "theory", "law", "principle", "formula",
    "experiment", "phenomenon", "hypothesis", "observation", "model",
    "measurement", "unit", "constant", "variable", "system", "process",
    "reaction", "property", "structure", "scale", "field",
    # physics
    "physics", "atom", "atoms", "atomic", "molecule", "molecular", "force",
    "energy", "gravity", "gravitational", "velocity", "acceleration",
    "electron", "electrons", "proton", "protons", "neutron", "neutrons",
    "quantum", "thermodynamics", "thermodynamic", "momentum", "wave",
    "waves", "frequency", "voltage", "current", "circuit", "magnet",
    "magnetic", "magnetism", "light", "photon", "photons", "mass",
    "friction", "pressure", "motion", "newton", "relativity", "entropy",
    "nuclear", "radiation", "radioactive", "electromagnetic", "optics",
    "kinetic", "potential", "density", "temperature", "heat", "power",
    "spectrum", "particle", "particles", "orbit", "inertia",
    # chemistry
    "chemistry", "chemical", "ph", "acid", "acidic", "base", "basic",
    "alkaline", "bond", "bonds", "bonding", "covalent", "ionic", "ion",
    "ions", "compound", "compounds", "catalyst", "periodic", "element",
    "elements", "isotope", "isotopes", "solution", "molarity", "oxidation",
    "reduction", "polymer", "polymers", "electrolyte", "solvent", "solute",
    "titration", "enthalpy", "equilibrium", "valence", "electronegativity",
    "hydrogen", "oxygen", "carbon", "nitrogen", "sodium", "chlorine",
    "reagent", "precipitate", "combustion", "corrosion", "buffer",
    # biology
    "biology", "biological", "cell", "cells", "cellular", "dna", "rna",
    "gene", "genes", "genetic", "genetics", "protein", "proteins",
    "enzyme", "enzymes", "evolution", "evolutionary", "photosynthesis",
    "mitosis", "meiosis", "ecosystem", "organism", "organisms", "virus",
    "viral", "bacteria", "bacterial", "chromosome", "chromosomes",
    "neuron", "neurons", "hormone", "hormones", "metabolism", "species",
    "mutation", "immune", "immunity", "vaccine", "microbiology", "anatomy",
    "physiology", "reproduction", "respiration", "ecology", "biodiversity",
    "taxonomy", "antibody", "pathogen",
    # math
    "math", "maths", "mathematics", "mathematical", "algebra",
    "algebraic", "calculus", "derivative", "derivatives", "integral",
    "equation", "equations", "function", "functions", "geometry",
    "geometric", "probability", "statistics", "statistical", "matrix",
    "matrices", "vector", "vectors", "theorem", "prime", "fraction",
    "fractions", "exponent", "exponential", "logarithm", "trigonometry",
    "trigonometric", "sine", "cosine", "tangent", "polynomial", "graph",
    "differential", "sequence", "series", "set", "sets", "proof", "angle",
    "triangle", "circle", "number", "numbers", "arithmetic", "ratio",
    # engineering / technology / computer science
    "engineering", "engineer", "technology", "technological", "algorithm",
    "algorithms", "software", "hardware", "robotics", "robot", "aerodynamics",
    "structural", "database", "network", "networking", "encryption",
    "compiler", "computer", "computing", "programming", "code", "data",
    "artificial", "intelligence", "machine", "learning", "internet",
    "processor", "semiconductor", "transistor", "satellite", "aerospace",
    "mechanical", "electrical", "civil", "materials", "sensor", "sensors",
    "battery", "renewable", "solar", "turbine", "rocket", "spacecraft",
    "signal", "bandwidth", "bit", "byte", "cpu", "gpu", "cryptography",
    # earth / space science
    "geology", "geological", "astronomy", "astronomical", "planet",
    "planets", "planetary", "star", "stars", "galaxy", "universe",
    "climate", "weather", "atmosphere", "atmospheric", "volcano",
    "earthquake", "tectonic", "ocean", "mineral", "rock", "fossil",
}


def _tokens(query: str) -> list[str]:
    return [w.lower() for w in _WORD_RE.findall(query) if len(w) > 1]


class RuleBasedValidator:
    """Deterministic structural + keyword-heuristic query validator."""

    def __init__(
        self,
        keywords: set[str] | None = None,
        *,
        min_length: int = MIN_LENGTH,
        max_length: int = MAX_LENGTH,
    ):
        self.keywords = keywords or STEM_KEYWORDS
        # Configurable per instance (see Settings.query_min_length/
        # query_max_length in src/config.py, wired up in
        # src/validation/__init__.py's build_validator()) - the module-level
        # MIN_LENGTH/MAX_LENGTH above remain as the defaults so constructing
        # a RuleBasedValidator() directly (e.g. in tests) still works.
        self.min_length = min_length
        self.max_length = max_length

    def structural_check(self, raw_query: str) -> str:
        """Raise InvalidQueryError for structurally unusable input, else
        return the trimmed query. This is failure case (1) from the spec:
        invalid query strings even when the length looks fine.
        """
        query = (raw_query or "").strip()
        if not query:
            raise InvalidQueryError("Query is empty or whitespace only.")
        if len(query) < self.min_length:
            raise InvalidQueryError(
                f"Query is too short (minimum {self.min_length} characters)."
            )
        if len(query) > self.max_length:
            raise InvalidQueryError(
                f"Query is too long (maximum {self.max_length} characters)."
            )
        words = _WORD_RE.findall(query)
        if not words:
            raise InvalidQueryError(
                "Query must contain actual words, not just symbols, "
                "punctuation, or numbers."
            )
        letters = sum(c.isalpha() for c in query)
        if letters / len(query) < MIN_ALPHA_RATIO:
            raise InvalidQueryError(
                "Query must be mostly alphabetic text, not symbols or digits."
            )
        return query

    def relevance_check(self, query: str) -> None:
        """Raise NotStemRelevantError if the (already structurally valid)
        query doesn't read as real, STEM-relevant text. This is failure
        case (2) from the spec.
        """
        tokens = _tokens(query)
        if not tokens:
            raise InvalidQueryError(
                "Query must contain actual words, not just symbols or numbers."
            )

        recognizable = [t for t in tokens if zipf_frequency(t, "en") >= ZIPF_KNOWN_THRESHOLD]
        if len(recognizable) / len(tokens) < MIN_REAL_WORD_RATIO:
            raise NotStemRelevantError(
                "Query doesn't appear to be made of real words, so it "
                "doesn't make semantic sense.",
                code="not_semantic",
            )

        if not self._matches_stem_vocabulary(tokens):
            raise NotStemRelevantError(
                "Query doesn't appear to be about a science, technology, "
                "engineering, or mathematics (STEM) topic."
            )

    def _matches_stem_vocabulary(self, tokens: list[str]) -> bool:
        long_keywords = [k for k in self.keywords if len(k) >= 4]
        for token in tokens:
            candidates = {token}
            if token.endswith("s") and len(token) > 3:
                candidates.add(token[:-1])  # crude singularization
            for candidate in candidates:
                if candidate in self.keywords:
                    return True
                if len(candidate) >= 4 and any(
                    candidate.startswith(k) or k.startswith(candidate)
                    for k in long_keywords
                ):
                    return True
        return False

    async def validate(self, raw_query: str) -> str:
        query = self.structural_check(raw_query)
        self.relevance_check(query)
        return query
