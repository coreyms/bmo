"""Jokes, riddles, and word games: routed to the LLM with a steering hint.
This is the 'plugin that augments the brain' pattern."""

from bmo.router import Plugin, Result


class JokesPlugin(Plugin):
    name = "jokes"
    priority = 25    # before games so "play twenty questions" never hits ROMs

    def __init__(self, app):
        super().__init__(app)
        self.add(r"\b(tell|say|know) (me |us )?(a |another |your best )?(joke|riddle)s?\b",
                 self.joke)
        self.add(r"\bknock knock\b", self.joke)
        self.add(r"\b(play |let's play )?(twenty|20) questions\b", self.twenty)
        self.add(r"\bplay (a |the )?(word|guessing|rhyming) game\b", self.word_game)
        self.add(r"\btell (me |us )?a story\b", self.story)

    def joke(self, m, text):
        return Result(brain_text=text,
                      style_hint="Respond with exactly ONE short, genuinely funny joke or riddle "
                                 "an 11-year-old would love, then stop. If a riddle, ask if they "
                                 "want the answer.")

    def twenty(self, m, text):
        return Result(brain_text=text,
                      style_hint="Start a game of twenty questions. Think of a kid-friendly "
                                 "thing and tell them to ask yes-or-no questions. Keep count.")

    def word_game(self, m, text):
        return Result(brain_text=text,
                      style_hint="Start a fun quick word game right now, explain the rules "
                                 "in two sentences, then take the first turn.")

    def story(self, m, text):
        return Result(brain_text=text,
                      style_hint="Tell a short fun adventure story, six to eight sentences, "
                                 "starring the listener as the hero.")
