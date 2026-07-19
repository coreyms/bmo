"""Jokes from a local bank (instant, pre-cachable audio) with LLM fallback;
riddles, stories, and word games go to the LLM with a steering hint."""

import json
import os
import random

from bmo.router import Plugin, Result


class JokesPlugin(Plugin):
    name = "jokes"
    priority = 25    # before games so "play twenty questions" never hits ROMs

    def __init__(self, app):
        super().__init__(app)
        self.bank = []
        self.used = set()
        path = app.cfg.path("assets/jokes.json")
        try:
            with open(path) as f:
                self.bank = json.load(f)
        except Exception:
            pass
        self.add(r"\b(tell|say|know) (me |us )?(a |another |your best )?riddles?\b",
                 self.riddle)
        self.add(r"\b(tell|say|know) (me |us )?(a |another |your best )?jokes?\b",
                 self.joke)
        self.add(r"\bknock knock\b", self.joke)
        self.add(r"\b(play |let's play )?(twenty|20) questions\b", self.twenty)
        self.add(r"\bplay (a |the )?(word|guessing|rhyming) game\b", self.word_game)
        self.add(r"\btell (me |us )?a story\b", self.story)

    def joke(self, m, text):
        fresh = [i for i in range(len(self.bank)) if i not in self.used]
        if not fresh:                      # bank exhausted: improvise via LLM
            return self._llm_joke(text)
        i = random.choice(fresh)
        self.used.add(i)
        j = self.bank[i]
        return Result(speech=f"{j['setup']}\n{j['punch']}")

    def riddle(self, m, text):
        return self._llm_joke(text)

    def _llm_joke(self, text):
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
