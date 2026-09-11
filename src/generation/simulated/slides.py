"""Slide content generation for the simulated provider.

No network calls, no API key: this is what keeps the simulated provider
fully self-contained (requirement 4's "simulated" half of plug-and-play).
For the curated set of topics below it draws on a small hand-written
knowledge base; for anything else it falls back to a generic structured
explainer template built from the query itself. That fallback is
intentionally modest - producing genuinely researched content for an
arbitrary open-domain question is exactly the job a real AI provider
(see src/generation/ai) is meant to take over.

Slide *count* scales with difficulty (requirement 19): content is always
authored as a full 9-beat outline (the max slide count, at "advanced"),
and select_beats() evenly samples it down for lower difficulties, always
keeping the intro and the "key takeaway" summary.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from src.domain.models import DifficultyLevel


@dataclass
class Beat:
    title: str
    bullets: list[str]
    narration: str


@dataclass
class Slide:
    index: int
    title: str
    bullets: list[str]
    narration: str


_STOPWORDS = {
    "how", "does", "do", "did", "why", "what", "is", "are", "the", "a",
    "an", "of", "to", "in", "on", "for", "and", "or", "between", "vs",
    "versus", "work", "works", "explain", "please", "can", "you", "me",
    "about", "i", "want", "know", "tell",
}


def _extract_topic_phrase(query: str) -> str:
    words = re.findall(r"[A-Za-z']+", query)
    content_words = [w for w in words if w.lower() not in _STOPWORDS]
    phrase = " ".join(content_words) if content_words else query.strip("? ")
    return phrase.strip() or query.strip("? ")


def _normalized_tokens(query: str) -> set[str]:
    return {w.lower() for w in re.findall(r"[A-Za-z]+", query)}


# --------------------------------------------------------------------------
# Curated knowledge base for the example topics called out in the spec.
# --------------------------------------------------------------------------

def _ph_scale_beats() -> list[Beat]:
    return [
        Beat(
            "What Is the pH Scale?",
            [
                "Measures how acidic or basic a water-based solution is",
                "Runs from 0 (very acidic) to 14 (very basic)",
                "7 is neutral - pure water sits right there",
            ],
            "The pH scale is a simple way scientists measure how acidic "
            "or basic a liquid is. It runs from zero, which is extremely "
            "acidic, up to fourteen, which is extremely basic, with seven "
            "sitting exactly in the middle as neutral.",
        ),
        Beat(
            "What pH Actually Measures",
            [
                "Tracks the concentration of hydrogen ions (H+) in a solution",
                "More H+ ions means a lower, more acidic pH",
                "Fewer H+ ions (more OH- ions) means a higher, more basic pH",
            ],
            "Underneath the number, pH is tracking the concentration of "
            "hydrogen ions floating in the solution. A liquid packed with "
            "hydrogen ions is acidic and scores low, while a liquid with "
            "very few of them, and more hydroxide ions instead, is basic "
            "and scores high.",
        ),
        Beat(
            "The Logarithmic Twist",
            [
                "Each whole step on the scale is a 10x change in acidity",
                "A pH of 4 is ten times more acidic than a pH of 5",
                "Small pH differences can matter a lot",
            ],
            "Here's the part that surprises people: the pH scale is "
            "logarithmic, not linear. Each single step represents a "
            "tenfold change, so a solution with a pH of four is ten times "
            "more acidic than one with a pH of five, and a hundred times "
            "more acidic than one with a pH of six.",
        ),
        Beat(
            "Everyday Examples",
            [
                "Battery acid: around pH 0-1",
                "Lemon juice: around pH 2",
                "Pure water: pH 7 (neutral)",
                "Household bleach: around pH 13",
            ],
            "You meet the pH scale constantly without noticing. Battery "
            "acid sits near zero, lemon juice is around two, plain water "
            "is a neutral seven, and household bleach climbs up toward "
            "thirteen.",
        ),
        Beat(
            "How pH Is Measured",
            [
                "Litmus paper gives a quick color-based estimate",
                "Digital pH meters give a precise numeric reading",
                "Indicator dyes change color at specific pH thresholds",
            ],
            "To actually measure pH, you can dip in litmus paper for a "
            "quick color-based estimate, use an indicator dye that shifts "
            "color at a known threshold, or use a digital pH meter for a "
            "precise numeric reading.",
        ),
        Beat(
            "Why the pH Scale Matters",
            [
                "Your blood stays tightly controlled near pH 7.4",
                "Soil pH determines which crops will thrive",
                "Ocean pH shifts affect coral reefs and marine life",
            ],
            "pH is not just a lab curiosity. Your own blood is tightly "
            "regulated near a pH of seven point four, farmers test soil "
            "pH to know what will grow, and even small shifts in ocean pH "
            "can stress coral reefs and marine ecosystems.",
        ),
        Beat(
            "Acids, Bases, and Neutralization",
            [
                "Mixing an acid and a base can neutralize both",
                "The reaction often produces water and a salt",
                "This is why antacids relieve an overly acidic stomach",
            ],
            "When an acid and a base meet, they can neutralize each "
            "other, usually producing water and a salt. That's the same "
            "chemistry behind an antacid tablet calming down a stomach "
            "that's become too acidic.",
        ),
        Beat(
            "Common Misconceptions",
            [
                "A low pH doesn't always mean 'dangerous'",
                "Concentration and pH are related but not identical",
                "Pure water is neutral, not 'pH-free'",
            ],
            "A couple of quick myth-busts: a low pH doesn't automatically "
            "mean something is dangerous, concentration and pH are "
            "related ideas but not the same thing, and pure water isn't "
            "somehow free of pH - it's simply neutral, sitting at seven.",
        ),
        Beat(
            "Key Takeaway",
            [
                "pH measures acidity/basicity from 0 to 14 on a log scale",
                "It's driven by hydrogen ion concentration",
                "It shows up everywhere from your blood to the ocean",
            ],
            "So, to sum it up: the pH scale runs from zero to fourteen on "
            "a logarithmic curve, it's really tracking hydrogen ion "
            "concentration, and it quietly shapes everything from the "
            "chemistry in your own blood to the health of the oceans.",
        ),
    ]


def _covalent_bonds_beats() -> list[Beat]:
    return [
        Beat(
            "Why Atoms Bond at All",
            [
                "Atoms are most stable with a full outer electron shell",
                "Bonding is how atoms reach that stable configuration",
                "Covalent bonding is one of the main ways they do it",
            ],
            "Atoms bond because a full outer shell of electrons is the "
            "most stable, lowest-energy arrangement they can be in. "
            "Bonding, including covalent bonding, is simply how atoms get "
            "there together.",
        ),
        Beat(
            "What a Covalent Bond Is",
            [
                "Two atoms share one or more pairs of electrons",
                "Both atoms count the shared electrons toward a full shell",
                "Common between two nonmetal atoms",
            ],
            "In a covalent bond, two atoms share one or more pairs of "
            "electrons between them. Each atom gets to count those shared "
            "electrons toward its own full outer shell, and this "
            "typically happens between two nonmetal atoms.",
        ),
        Beat(
            "Why Sharing Beats Losing Electrons",
            [
                "Some atoms hold their electrons too tightly to give them up",
                "Sharing lets both atoms reach stability without fully losing one",
                "It's a compromise that lowers the system's overall energy",
            ],
            "For two atoms that both grip their electrons tightly, giving "
            "one up entirely is too costly. Sharing is a compromise: both "
            "atoms get the stability benefit without either one fully "
            "losing an electron, and the whole system ends up at a "
            "lower, more stable energy.",
        ),
        Beat(
            "Single, Double, and Triple Bonds",
            [
                "A single bond shares one pair of electrons",
                "A double bond shares two pairs (like in O2)",
                "A triple bond shares three pairs (like in N2)",
            ],
            "Covalent bonds come in different strengths. A single bond "
            "shares one pair of electrons, a double bond - like the one "
            "holding oxygen gas together - shares two pairs, and a triple "
            "bond, as in nitrogen gas, shares three pairs.",
        ),
        Beat(
            "Everyday Examples",
            [
                "Water (H2O): oxygen shares electrons with two hydrogens",
                "Carbon dioxide (CO2): carbon double-bonds to two oxygens",
                "Methane (CH4): carbon shares electrons with four hydrogens",
            ],
            "You're surrounded by covalent molecules. Water forms when "
            "oxygen shares electrons with two hydrogen atoms, carbon "
            "dioxide forms through double bonds between carbon and two "
            "oxygens, and methane forms when carbon shares electrons with "
            "four separate hydrogen atoms.",
        ),
        Beat(
            "Polar vs Nonpolar Covalent Bonds",
            [
                "Nonpolar: atoms share electrons roughly equally",
                "Polar: one atom pulls the shared electrons closer",
                "That pull is called electronegativity",
            ],
            "Not all sharing is equal. In a nonpolar covalent bond the "
            "atoms share electrons roughly evenly, but in a polar "
            "covalent bond one atom - the more electronegative one - "
            "pulls the shared electrons closer to itself, creating a "
            "slightly uneven charge.",
        ),
        Beat(
            "Properties of Covalent Compounds",
            [
                "Often lower melting and boiling points than ionic compounds",
                "Frequently don't conduct electricity well",
                "Can exist as gases, liquids, or solids at room temperature",
            ],
            "Because covalent bonds form individual molecules rather than "
            "a rigid charged lattice, covalent compounds often have lower "
            "melting and boiling points, usually conduct electricity "
            "poorly, and can be found as gases, liquids, or solids "
            "depending on the substance.",
        ),
        Beat(
            "Key Takeaway",
            [
                "Covalent bonds form when atoms share electron pairs",
                "Sharing lets both atoms reach a stable outer shell",
                "Bond count (single/double/triple) affects strength",
            ],
            "In short: covalent bonds form when atoms share pairs of "
            "electrons so both can reach a stable, full outer shell, and "
            "whether that sharing is single, double, or triple changes "
            "how strong the resulting bond is.",
        ),
    ]


def _ionic_vs_covalent_beats() -> list[Beat]:
    return [
        Beat(
            "Two Ways Atoms Bond",
            [
                "Ionic bonding: electrons are transferred between atoms",
                "Covalent bonding: electrons are shared between atoms",
                "Both are ways atoms reach a stable outer electron shell",
            ],
            "Atoms have two main strategies for reaching a stable outer "
            "shell of electrons: in ionic bonding they transfer electrons "
            "from one atom to another, and in covalent bonding they share "
            "electrons between them.",
        ),
        Beat(
            "How Ionic Bonds Form",
            [
                "A metal atom loses one or more electrons",
                "A nonmetal atom gains those electrons",
                "The resulting oppositely-charged ions attract each other",
            ],
            "An ionic bond typically starts with a metal atom giving up "
            "one or more electrons entirely to a nonmetal atom. That "
            "leaves the metal positively charged and the nonmetal "
            "negatively charged, and those opposite charges attract each "
            "other strongly.",
        ),
        Beat(
            "How Covalent Bonds Form",
            [
                "Two nonmetal atoms both hold electrons tightly",
                "Instead of transferring, they share electron pairs",
                "Both atoms count the shared pair toward a full shell",
            ],
            "A covalent bond usually forms between two nonmetal atoms "
            "that each grip their electrons too tightly to give one up. "
            "Instead they share pairs of electrons, and both atoms count "
            "that shared pair toward their own stable outer shell.",
        ),
        Beat(
            "Which Elements Are Involved",
            [
                "Ionic: typically metal + nonmetal (e.g. sodium + chlorine)",
                "Covalent: typically nonmetal + nonmetal (e.g. two oxygens)",
                "Electronegativity difference is the key predictor",
            ],
            "A quick rule of thumb: ionic bonds usually form between a "
            "metal and a nonmetal, like sodium and chlorine, while "
            "covalent bonds usually form between two nonmetals, like two "
            "oxygen atoms. The underlying predictor is how different the "
            "atoms' electronegativities are.",
        ),
        Beat(
            "Structure: Lattice vs Molecule",
            [
                "Ionic compounds form repeating crystal lattices",
                "Covalent compounds form discrete individual molecules",
                "This structural difference drives most other differences",
            ],
            "Ionic compounds don't form single molecules - they build "
            "vast repeating crystal lattices of alternating positive and "
            "negative ions. Covalent compounds, by contrast, form "
            "discrete individual molecules, and that structural "
            "difference is behind most of their other contrasting "
            "properties.",
        ),
        Beat(
            "Melting Point and Conductivity",
            [
                "Ionic: high melting points, conducts when dissolved or molten",
                "Covalent: lower melting points, usually poor conductors",
                "Ionic lattices need lots of energy to break apart",
            ],
            "Because ionic lattices are held together by strong "
            "electrostatic attraction throughout the whole crystal, they "
            "tend to have high melting points and conduct electricity "
            "once dissolved or melted. Covalent compounds generally melt "
            "at lower temperatures and are poor conductors.",
        ),
        Beat(
            "Real-World Examples",
            [
                "Ionic: table salt (NaCl), calcium chloride",
                "Covalent: water (H2O), glucose, most organic molecules",
                "Some substances even show characteristics of both",
            ],
            "Table salt and calcium chloride are classic ionic compounds, "
            "while water, glucose, and most organic molecules are "
            "covalent. Some more complex substances even show a blend of "
            "both types of bonding character.",
        ),
        Beat(
            "Key Takeaway",
            [
                "Ionic bonds transfer electrons; covalent bonds share them",
                "Ionic compounds form crystal lattices with high melting points",
                "Covalent compounds form molecules, often with lower melting points",
            ],
            "The core difference: ionic bonding transfers electrons "
            "between a metal and a nonmetal to build a crystal lattice, "
            "while covalent bonding shares electrons between nonmetals to "
            "build individual molecules - and that one structural "
            "difference explains most of the contrast in their "
            "properties.",
        ),
    ]


def _generic_beats(topic_phrase: str) -> list[Beat]:
    t = topic_phrase.strip() or "this topic"
    return [
        Beat(
            f"Introducing {t.title()}",
            [
                f"A closer look at {t}",
                "Why this topic comes up in STEM classrooms",
                "What you'll take away from this explainer",
            ],
            f"Let's take a closer look at {t}. It's a topic that comes up "
            f"often in science, technology, engineering, and mathematics, "
            f"and by the end of this explainer you'll have a solid mental "
            f"model of the basics.",
        ),
        Beat(
            "The Core Idea",
            [
                f"The essential concept behind {t}",
                "How it fits into the broader subject area",
                "The key terms worth knowing",
            ],
            f"At its core, {t} comes down to a small set of ideas that "
            f"build on each other. Understanding the key terms is the "
            f"fastest way into the rest of the topic.",
        ),
        Beat(
            "How It Works",
            [
                "The step-by-step mechanism",
                "What causes it to happen",
                "How scientists or engineers study it",
            ],
            f"Understanding how {t} actually works means walking through "
            f"the mechanism step by step, and seeing what causes it to "
            f"happen the way it does.",
        ),
        Beat(
            "A Concrete Example",
            [
                f"A real, everyday example involving {t}",
                "What you can observe or measure",
                "How the underlying concept shows up in practice",
            ],
            f"A concrete example makes {t} click: you can often observe "
            f"or measure it directly, which is where the underlying "
            f"concept turns from an abstract idea into something real.",
        ),
        Beat(
            "Common Misconceptions",
            [
                f"A frequent misunderstanding about {t}",
                "Why that misunderstanding happens",
                "The clearer way to think about it",
            ],
            f"There's a common misconception people run into with {t}. "
            f"Understanding why that mix-up happens is often the "
            f"clearest way to really understand the correct idea.",
        ),
        Beat(
            "Why It Matters",
            [
                "Where this concept shows up in the real world",
                "Fields or careers that rely on it",
                "Why it's worth understanding",
            ],
            f"{t.capitalize()} isn't just theory - it shows up in real "
            f"applications and in the work of scientists and engineers "
            f"who rely on it every day.",
        ),
        Beat(
            "Related Concepts",
            [
                "Ideas that connect to this topic",
                "What to study next",
                "How this fits the bigger picture",
            ],
            f"Once {t} makes sense, a few related concepts naturally "
            f"come next, and together they build toward the bigger "
            f"picture of the subject.",
        ),
        Beat(
            "Quick Recap",
            [
                "The core idea, restated simply",
                "The mechanism in one sentence",
                "The one thing to remember",
            ],
            f"Quick recap: {t} comes down to a core idea and a mechanism "
            f"that explains why it happens - and that's the one thing "
            f"worth remembering.",
        ),
        Beat(
            "Key Takeaway",
            [
                f"{t.capitalize()}, in one sentence",
                "Why it's worth understanding",
                "Where to explore next",
            ],
            f"So the key takeaway on {t}: it's a foundational STEM idea "
            f"that's well worth understanding, and a great topic to keep "
            f"exploring further.",
        ),
    ]


def _matches_all(tokens: set[str], *words: str) -> bool:
    return all(w in tokens for w in words)


def build_beats(query: str) -> list[Beat]:
    """Pick the richest content source available for this query.

    Order matters: the ionic-vs-covalent comparison is checked before the
    single-topic covalent-bonds content so a comparison question gets the
    comparison beats rather than a one-sided explanation.
    """
    tokens = _normalized_tokens(query)
    if _matches_all(tokens, "ionic", "covalent"):
        return _ionic_vs_covalent_beats()
    if "covalent" in tokens:
        return _covalent_bonds_beats()
    if "ph" in tokens and tokens & {"scale", "acid", "acidic", "base", "basic"}:
        return _ph_scale_beats()
    return _generic_beats(_extract_topic_phrase(query))


def select_beats(beats: list[Beat], n: int) -> list[Beat]:
    """Evenly sample `n` beats out of the full outline, always keeping the
    first (intro) and last (key takeaway) beat.
    """
    n = max(1, min(n, len(beats)))
    if n == len(beats):
        return list(beats)
    if n == 1:
        return [beats[0]]
    indices = sorted({round(i * (len(beats) - 1) / (n - 1)) for i in range(n)})
    if len(indices) < n:
        for i in range(len(beats)):
            if len(indices) >= n:
                break
            if i not in indices:
                indices.append(i)
        indices = sorted(indices)
    return [beats[i] for i in indices[:n]]


def build_slides(query: str, difficulty: DifficultyLevel) -> list[Slide]:
    beats = build_beats(query)
    chosen = select_beats(beats, difficulty.slide_count)
    return [
        Slide(index=i, title=b.title, bullets=b.bullets, narration=b.narration)
        for i, b in enumerate(chosen)
    ]


def fit_narration_to_budget(text: str, max_words: int) -> str:
    """Trim narration to at most `max_words`, preferring a sentence
    boundary so the audio never cuts off mid-thought."""
    words = text.split()
    if len(words) <= max_words:
        return text
    sentences = re.split(r"(?<=[.!?])\s+", text)
    kept: list[str] = []
    count = 0
    for sentence in sentences:
        sentence_words = sentence.split()
        if count + len(sentence_words) > max_words and kept:
            break
        kept.append(sentence)
        count += len(sentence_words)
    if kept:
        return " ".join(kept)
    return " ".join(words[:max_words]) + "..."
