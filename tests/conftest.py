import os

# a tesztek alatt a szerver ne induljon háttér-gyűjtéssel, és ne érje el a valódi API-t / GitHubot
os.environ["ESOCCER_BATTLE_NO_SYNC"] = "1"
os.environ["ESOCCER_BATTLE_NO_SNAPSHOT"] = "1"
os.environ["ESOCCER_BATTLE_NO_UPDATE_CHECK"] = "1"
