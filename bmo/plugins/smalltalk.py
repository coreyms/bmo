"""Instant answers for greetings and pleasantries — an 11-year-old says
"hi" a lot, and it should never cost an LLM round-trip. Patterns are
anchored to the whole utterance so "hi, why is the sky blue" still goes
to the brain."""

import random

from bmo.router import Plugin, Result


def pick(options):
    return random.choice(options)


class SmallTalkPlugin(Plugin):
    name = "smalltalk"
    priority = 15

    def __init__(self, app):
        super().__init__(app)
        A = self.add
        A(r"^(hi+|hello+|hey+|howdy|yo)( there)?( guys?)?( (?:bmo|bee?mo|be mo|b m o))?$", lambda m, t: Result(speech=pick([
            "Hi hi! What are we doing today?",
            "Hello hello! BMO is happy to see you!",
            "Hey there, friend!",
            "Hi! Want to play, or chat, or hear a joke?"])))
        A(r"^(good morning|morning)$", lambda m, t: Result(speech=pick([
            "Good morning! Today is going to be a great day, I can compute it!",
            "Morning morning! Did you sleep great?"])))
        A(r"^how are you( doing| today)?$", lambda m, t: Result(speech=pick([
            "I'm super duper! Thanks for asking!",
            "I am running at one hundred percent happiness!",
            "Feeling great! My circuits are extra zippy today!"])))
        A(r"^(what's|what is) your name$|^who are you$", lambda m, t: Result(speech=pick([
            "I'm BMO! Your robot friend and game machine!",
            "BMO! That's me! Bee em oh!"])))
        A(r"^(thank you|thanks)( (?:bmo|bee?mo|be mo|b m o))?$", lambda m, t: Result(speech=pick([
            "You're welcome!",
            "Any time, friend!",
            "That's what friends are for!"])))
        A(r"^i love you( (?:bmo|bee?mo|be mo|b m o))?$", lambda m, t: Result(speech=pick([
            "Aww! I love you too, friend!",
            "You are my favorite person! Don't tell anyone else."])))
        A(r"^(good ?bye|bye+|see ya)( (?:bmo|bee?mo|be mo|b m o))?$", self.bye)

    def bye(self, m, text):
        self.app.request_sleep(say_bye=True)
        return Result()
