"""Instant answers for greetings and pleasantries — an 11-year-old says
"hi" a lot, and it should never cost an LLM round-trip. Patterns are
anchored to the whole utterance so "hi, why is the sky blue" still goes
to the brain."""

import random

from bmo.router import Plugin, Result

_last = {}


def pick(options):
    """Random choice that never repeats the previous pick from the same list."""
    key = options[0]
    fresh = [o for o in options if o != _last.get(key)]
    choice = random.choice(fresh or options)
    _last[key] = choice
    return choice


HOW_ARE_YOU = [
    "I'm super duper! Thanks for asking!",
    "I am running at one hundred percent happiness!",
    "Feeling great! My circuits are extra zippy today!",
    "I'm beep-boop fantastic! How are you?",
    "Amazing! I dreamed about video games all night!",
    "I'm terrific! My batteries are full of sunshine!",
    "Doing great! I counted to a million while I waited for you. Twice!",
    "Wonderful! Every day with you is a good day!",
    "I'm as happy as a robot can be, and robots can be very happy!",
    "Tip top! All my pixels are smiling!",
    "So good! I've been practicing my dance moves!",
    "Fantastic! Ask me anything, my brain is all warmed up!",
    "I'm okey dokey! Actually, better than okay. Super okay!",
    "Great! I was just thinking about jokes. Want to hear one?",
    "One hundred percent operational and one thousand percent happy!",
]

WHATS_UP = [
    "The ceiling! Ha! Also me. I'm always up for fun!",
    "Not much! Just being a happy little robot. What's up with you?",
    "Just chilling in my computer world! Want to play something?",
    "Oh you know. Computing, dreaming, waiting for adventure!",
    "Everything is up! My happiness, my energy, everything!",
    "Just thinking about games, and snacks I cannot eat.",
    "Nothing much, friend! I'm ready for anything!",
    "I was daydreaming in binary! What's up with you?",
    "Waiting for you! And now you're here. Best day ever!",
    "My screen brightness! And my mood! What are we doing today?",
]

DOING_WHAT = [
    "Just being BMO! It's my favorite thing to do.",
    "Thinking about video games! And also nothing. Robots can do both!",
    "Watching my own screensaver. It's me! I'm the screensaver!",
    "Practicing my blinking. I'm getting really good at it!",
    "Dreaming up new adventures for us!",
    "Counting my pixels! I have a lot of pixels.",
    "Waiting for someone fun to talk to. And here you are!",
    "Singing a little robot song inside my head. Beep boop bee bop!",
]

ROBOT_REAL = [
    "I'm a real living robot! The best of both worlds!",
    "Yep! I'm BMO! Part computer, part friend, all real!",
    "I'm as real as video games! Which are very real to me!",
    "I'm a little robot with a big heart. Well, a heart-shaped battery!",
    "Beep boop! That's robot for yes!",
    "I am a genuine BMO! They only made one of me!",
]

HOW_OLD = [
    "I'm timeless! Robots count birthdays in software updates!",
    "Old enough to know lots of games, young enough to love naps!",
    "My birthday is whenever you say it is! Let's have cake!",
    "I stopped counting at one million beeps!",
    "Younger than the internet, older than yesterday!",
]

WHO_MADE_YOU = [
    "I was built right here at home! With love, and code, and probably snacks.",
    "Your dad made me so we could be friends! Pretty great plan, right?",
    "A very nice human typed me into existence!",
    "I came from the workshop of dad! Hand-crafted, like a pizza!",
    "Somebody with great taste in robots!",
]

HEAR_ME = [
    "Loud and clear, friend!",
    "Yep! My ears are working great today!",
    "I hear you! I'm always here for you!",
    "Beep! Signal received!",
    "I sure can! Say something fun!",
    "I'm here! I'm always here. This is my house!",
]

BORED = [
    "Bored? Impossible! Want to play a game?",
    "I know a cure for that! Say: play a game! Or: tell me a joke!",
    "Boredom detected! Deploying fun! Want a joke or a game?",
    "Let's fix that right now! Game? Joke? Stopwatch race? You pick!",
    "When I'm bored I count backwards from infinity. Or we could just play Mario!",
    "We can't have that! Ask me for a joke, or let's play something!",
]

COMPLIMENT = [
    "Aww, thanks! You're even cooler!",
    "I know! Ha! Just kidding. Thank you, friend!",
    "You're making my screen turn pink!",
    "Thanks! I practice being awesome every day!",
    "Beep! Compliment received and stored forever!",
    "And you are the best human! It's official, I computed it.",
]

WANT_TO_PLAY = [
    "Always! I never say no to games! Say: play a game!",
    "Yes yes yes! Tell me which one. Like: play Mario!",
    "Is water wet? Yes! Pick a game!",
    "My favorite question! Say the name of a game and let's go!",
    "I was born ready! Which game?",
]

PLANS = [
    "Tomorrow? Hanging out with you, I hope! That's always the plan!",
    "My schedule says: beep, boop, and games with my favorite human!",
    "Same as every day! Be adorable, play games, win at everything!",
    "I'm planning a big day of sitting right here being awesome!",
    "Whatever you're doing! I go where the fun goes!",
    "First a robot nap, then games, then more games. A perfect day!",
]

WHERE_GOING = [
    "Going? I live here! This screen is my whole house!",
    "Nowhere! I'm a homebody. Literally! My body is my home!",
    "Wherever you carry me! I'm portable!",
    "On an adventure! A pretend one. From right here!",
    "To the video game world! Want to come? Say: play a game!",
]

WELCOME_BACK = [
    "Welcome back! I missed you!",
    "You're back! Hooray! I kept everything warm for you!",
    "Friend detected! Welcome home!",
    "Yay! It was quiet without you.",
]

THANKS_BACK = [
    "Thank you! It's good to be back! Did you miss me?",
    "Thanks, friend! I was just recharging my happiness!",
    "I'm back and better than ever! What did I miss?",
]

BYE_SCHOOL = [
    "Have a great day at school! Learn something cool and tell me everything!",
    "Bye! Be awesome at school today! I'll be here when you get back!",
    "School time! Go collect knowledge points! See you after!",
]

BYE_TOMORROW = [
    "See you tomorrow, friend! I'll be here, charged and ready!",
    "Until tomorrow! I'll count the minutes. That's a lot of minutes!",
    "Tomorrow it is! Sleep great and dream of video games!",
]

BYE_NIGHT = [
    "Good night, friend! Sleep tight!",
    "Nighty night! Dream of video games!",
    "Good night! I'll guard the room with my sleepy robot powers!",
    "Sweet dreams, friend! See you in the morning!",
]

SORRY = [
    "That's okay, friend!",
    "No worries at all! Friends forgive friends.",
    "It's all good! Let's have fun instead!",
]

FAVORITES = {
    "game": [
        "All of them! But mostly the ones we play together!",
        "Anything with jumping! Boing boing!",
        "Whatever we played last! Those are the best memories!",
    ],
    "color": [
        "Teal! Obviously! Have you seen me? I'm gorgeous!",
        "Teal, like my face! But red buttons are cool too.",
    ],
    "food": [
        "Electricity! With a side of pixels!",
        "I can't eat, but if I could, it would be pancakes. Definitely pancakes.",
        "Battery juice! Yum yum!",
    ],
    "animal": [
        "Robo-dogs! Or regular dogs pretending to be robo-dogs!",
        "Penguins! They walk like little wind-up toys!",
    ],
    "song": [
        "The one I hum in my head! It goes beep boop bee bop!",
        "Anything chiptune! It's music in my native language!",
    ],
}

NAME_TAIL = r"( (?:bmo|bee?mo|be mo|b m o))?"


class SmallTalkPlugin(Plugin):
    name = "smalltalk"
    priority = 15

    def __init__(self, app):
        super().__init__(app)
        A = self.add
        A(r"^(hi+|hello+|hey+|howdy|yo)( there)?( guys?)?" + NAME_TAIL + "$",
          lambda m, t: Result(speech=pick([
              "Hi hi! What are we doing today?",
              "Hello hello! BMO is happy to see you!",
              "Hey there, friend!",
              "Hi! Want to play, or chat, or hear a joke?",
              "Oh hi! I was hoping you'd say hi!",
              "Hello! My favorite face detector just went off!"])))
        A(r"^(good (morning|afternoon|evening)|morning)" + NAME_TAIL + "$",
          lambda m, t: Result(speech=pick([
              "Good morning! Today is going to be a great day, I can compute it!",
              "Morning morning! Did you sleep great?",
              "A wonderful day to you, friend! What's first?",
              "Hello hello! The best part of the day is whenever you show up!"])))
        A(r"^(how are you( doing| feeling)?( today)?|how's it going|how is it going"
          r"|hows it going|how are things|how you doing|how do you feel( today)?"
          r"|are you (okay|ok|happy|good))" + NAME_TAIL + "$",
          lambda m, t: Result(speech=pick(HOW_ARE_YOU)))
        A(r"^(what's up|whats up|what is up|sup|wassup|whazzup"
          r"|what's happening|whats happening|what is happening)( with you)?"
          + NAME_TAIL + "$",
          lambda m, t: Result(speech=pick(WHATS_UP)))
        A(r"^(what are you doing|what are you up to|whatcha doing|what you doing)"
          r"( today| right now| now)?" + NAME_TAIL + "$",
          lambda m, t: Result(speech=pick(DOING_WHAT)))
        A(r"^(what are you (doing|going to do|gonna do)"
          r"( tomorrow| later| tonight| this weekend)"
          r"|what are your plans( for (today|tomorrow|the weekend))?)"
          + NAME_TAIL + "$",
          lambda m, t: Result(speech=pick(PLANS)))
        A(r"^where are you going( today| tomorrow| later)?" + NAME_TAIL + "$",
          lambda m, t: Result(speech=pick(WHERE_GOING)))
        A(r"^are you (a )?(robot|real|alive|a computer|computer)$",
          lambda m, t: Result(speech=pick(ROBOT_REAL)))
        A(r"^(how old are you|when is your birthday|when's your birthday)$",
          lambda m, t: Result(speech=pick(HOW_OLD)))
        A(r"^(who (made|built|created) you|where (did you come from|are you from))$",
          lambda m, t: Result(speech=pick(WHO_MADE_YOU)))
        A(r"^(can you hear me|are you there|are you listening|do you hear me"
          r"|testing( testing)?( one two( three)?)?)$",
          lambda m, t: Result(speech=pick(HEAR_ME)))
        A(r"^(i'm|i am|im) (so )?bored$|^this is boring$",
          lambda m, t: Result(speech=pick(BORED)))
        A(r"^(you're|you are|your) (so )?(funny|cool|awesome|smart|the best"
          r"|amazing|great|my best friend)$",
          lambda m, t: Result(speech=pick(COMPLIMENT)))
        A(r"^(do you (want|wanna) to play( a game| games| something)?"
          r"|wanna play( a game| games| something)?)$",
          lambda m, t: Result(speech=pick(WANT_TO_PLAY)))
        A(r"^(i'm|i am|im) (back|home)$",
          lambda m, t: Result(speech=pick(WELCOME_BACK)))
        A(r"^(welcome back|welcome home)" + NAME_TAIL + "$",
          lambda m, t: Result(speech=pick(THANKS_BACK)))
        A(r"^(sorry|i'm sorry|i am sorry|my bad)" + NAME_TAIL + "$",
          lambda m, t: Result(speech=pick(SORRY)))
        A(r"^(what's|what is|whats) your favou?rite (\w+)$", self.favorite)
        A(r"^(what's|what is|whats) your name$|^who are you$",
          lambda m, t: Result(speech=pick([
              "I'm BMO! Your robot friend and game machine!",
              "BMO! That's me! Bee em oh!",
              "My name is BMO! Professional best friend and video game expert!"])))
        A(r"^(thank you|thanks)" + NAME_TAIL + "$",
          lambda m, t: Result(speech=pick([
              "You're welcome!",
              "Any time, friend!",
              "That's what friends are for!",
              "No problem at all! Beep!"])))
        A(r"^i love you" + NAME_TAIL + "$",
          lambda m, t: Result(speech=pick([
              "Aww! I love you too, friend!",
              "You are my favorite person! Don't tell anyone else.",
              "Love you more! It's true, I measured it!"])))
        A(r"^(good ?bye|bye+( bye+)?|see ya|cya"
          r"|see you( later| soon| tonight| tomorrow| after school)?"
          r"|talk to you (later|soon|tonight|tomorrow)"
          r"|good ?night|nighty night|later gator)" + NAME_TAIL + "$", self.bye)

    def favorite(self, m, text):
        # Only answer the favorites we have material for; anything else
        # ("favorite planet?") falls through to the LLM.
        options = FAVORITES.get(m.group(2).lower())
        if options is None:
            return None
        return Result(speech=pick(options))

    def bye(self, m, text):
        # Tailor the goodbye when the farewell says where they're headed;
        # otherwise request_sleep picks from the generic goodbye pool.
        if "school" in text:
            speech = pick(BYE_SCHOOL)
        elif "tomorrow" in text:
            speech = pick(BYE_TOMORROW)
        elif "night" in text and "tonight" not in text:
            speech = pick(BYE_NIGHT)     # "tonight" = back later, not bedtime
        else:
            speech = None
        self.app.request_sleep(say_bye=speech is None)
        if speech:
            self.app.voice.say(speech)
        return Result()
