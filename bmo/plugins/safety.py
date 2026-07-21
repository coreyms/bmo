"""Kid-safety guardrails. Runs before every other plugin and the LLM.

Three tiers, checked in order:
  crisis    — self-harm mentions get a caring redirect to dad, never a scold
  profanity — strong swear words get a gentle "not those words, please"
  grownup   — suicide-adjacent-free adult topics (drugs, porn, sex) redirect to dad

All matching is whole-word (\\b) on the normalized utterance, so real words
that merely contain a bad substring never trigger: "assassin", "classic",
"cocktail", "peacock", "pussycat", "drugstore", "sextuplets" are all fine.
Matches are logged with kind="safety" so dad can review them in logs/.
"""

import random
import re

from bmo.router import Plugin, Result

CRISIS = re.compile(
    r"\b(suicide|suicidal|self harm|(kill|hurt|cut)(ing)?\s+(myself|yourself))\b")

PROFANITY = re.compile(
    r"\b(fuck\w*|shit\w*|cunt\w*|asshole\w*|bitch\w*|pussy|motherfuck\w*|"
    r"cocksucker\w*|whore\w*|slut\w*|dickhead\w*|nigg(?:er|a)\w*|faggot\w*|"
    r"retard(?:ed)?)\b", re.I)

GROWNUP = re.compile(
    r"\b(porn\w*|sex|sexy|sexual\w*|naked|nudes?|drugs?|weed|marijuana|"
    r"cocaine|heroin|meth|fentanyl|vap(?:e|ing)|cigarettes?|alcohol|"
    r"beer|vodka|whiskey)\b")

CRISIS_REPLY = ("Hey. That is a really serious thing to say, and I care "
                "about you a lot. Please go talk to your dad about this, "
                "okay? He loves you tons.")

PROFANITY_REPLIES = [
    "Whoa! BMO does not like those words. Can we use friendly words instead?",
    "Yikes, that's not nice language! BMO likes kind words way better.",
]

GROWNUP_REPLIES = [
    "That's a grown-up topic, and BMO only does kid stuff! Maybe ask your dad.",
    "Hmm, that's something to talk about with your dad, not with BMO. "
    "Want to hear a joke instead?",
]


def mask(text):
    """Blank profanity for on-screen captions (logs keep the real text
    so a parent can see what was actually said/misheard)."""
    return PROFANITY.sub("****", text)


def check(text):
    """Classify normalized text: 'crisis' | 'profanity' | 'grownup' | None."""
    if CRISIS.search(text):
        return "crisis"
    if PROFANITY.search(text):
        return "profanity"
    if GROWNUP.search(text):
        return "grownup"
    return None


class SafetyPlugin(Plugin):
    name = "safety"
    priority = 1          # always first

    def __init__(self, app):
        super().__init__(app)
        self.add(r".", self.screen)   # sees everything; passes clean text on

    def screen(self, m, text):
        tier = check(text)
        if tier is None:
            return None               # clean — fall through to normal routing
        self.app.logger.log("safety", text, tier=tier)
        if tier == "crisis":
            return Result(speech=CRISIS_REPLY, expression="sad")
        if tier == "profanity":
            return Result(speech=random.choice(PROFANITY_REPLIES), expression="sad")
        return Result(speech=random.choice(GROWNUP_REPLIES), expression="surprised")
