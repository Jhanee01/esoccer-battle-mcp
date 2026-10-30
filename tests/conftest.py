import os

# a tesztek alatt a szerver ne induljon háttér-gyűjtéssel a valódi API felé
os.environ["ESOCCER_BATTLE_NO_SYNC"] = "1"
