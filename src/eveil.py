#!/usr/bin/env python3
"""
eveil.py : garde le PC ET l'ecran allumes pendant que le bot travaille, comme si quelqu'un utilisait le PC.

Pourquoi : si l'ecran se verrouille (inactivite), le jeu est deconnecte.
Comment (Windows seulement, rien a installer, aucun reglage de Windows modifie) :
  1. SetThreadExecutionState (comme un lecteur video) : pas de mise en veille, ecran jamais eteint, pas d'economiseur d'ecran ;
  2. si personne n'a touche au clavier ni a la souris depuis 4 minutes, le bot appuie sur F15 (une touche qui n'existe pas
     sur les claviers et ne fait rien dans les programmes) : Windows compte une activite et ne verrouille pas la session
     (meme principe que l'outil "Caffeine"). Quand tu utilises le PC, rien n'est envoye.
A l'arret du bot (ou s'il plante), Windows reprend tout seul ses reglages normaux.
"""
import os
import threading

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002
VK_F15 = 0x7E
KEYEVENTF_KEYUP = 0x0002
INACTIVITE_MAX = 240            # secondes sans clavier ni souris avant d'appuyer sur F15
PERIODE = 30                    # secondes entre deux verifications


class SystemeWindows:
    """Appels a Windows (ctypes)."""
    def __init__(self):
        import ctypes
        self.ctypes = ctypes
        self.k32, self.u32 = ctypes.windll.kernel32, ctypes.windll.user32

        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]
        self.LASTINPUTINFO = LASTINPUTINFO
        self.k32.SetThreadExecutionState.restype = ctypes.c_uint
        self.k32.GetTickCount.restype = ctypes.c_uint

    def garder(self, actif):
        etat = ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED if actif else ES_CONTINUOUS
        return self.k32.SetThreadExecutionState(self.ctypes.c_uint(etat)) != 0

    def inactif_depuis(self):
        """Secondes depuis la derniere action au clavier ou a la souris (sur ce PC)."""
        lii = self.LASTINPUTINFO()
        lii.cbSize = self.ctypes.sizeof(lii)
        if not self.u32.GetLastInputInfo(self.ctypes.byref(lii)):
            return 0.0
        return ((self.k32.GetTickCount() - lii.dwTime) & 0xFFFFFFFF) / 1000.0

    def toucher(self):
        self.u32.keybd_event(VK_F15, 0, 0, 0)
        self.u32.keybd_event(VK_F15, 0, KEYEVENTF_KEYUP, 0)


class GarderEveille:
    """`with GarderEveille(sortie):` autour du travail du bot. Sans Windows : ne fait rien."""
    def __init__(self, sortie=print, systeme=None, periode=PERIODE, inactivite_max=INACTIVITE_MAX):
        self.sortie, self.periode, self.inactivite_max = sortie, periode, inactivite_max
        self.systeme = systeme
        self.arret = threading.Event()
        self.fil = None
        self.touches = 0

    def __enter__(self):
        if self.systeme is None:
            if os.name != "nt":
                return self
            try:
                self.systeme = SystemeWindows()
            except Exception as e:                      # jamais bloquant pour le bot
                self.sortie("ATTENTION : impossible de garder l'ecran allume (%s)." % e)
                return self
        self.fil = threading.Thread(target=self._boucle, name="eveil", daemon=True)
        self.fil.start()
        self.sortie("Ecran et PC gardes allumes pendant que le bot travaille (pas de veille ni de verrouillage).")
        return self

    def _boucle(self):
        # l'etat "ecran requis" appartient a ce fil : il reste actif tant que le fil vit, et s'efface tout seul a sa fin
        try:
            while not self.arret.is_set():
                self.systeme.garder(True)
                if self.systeme.inactif_depuis() >= self.inactivite_max:
                    self.systeme.toucher()
                    self.touches += 1
                self.arret.wait(self.periode)
        except Exception:
            pass
        finally:
            try:
                self.systeme.garder(False)
            except Exception:
                pass

    def __exit__(self, *exc):
        self.arret.set()
        if self.fil is not None:
            self.fil.join(timeout=5)
        return False


def console_sans_selection():
    """Windows : desactive le "mode selection rapide" (QuickEdit) de la fenetre du bot. Sinon, un simple clic dans la fenetre
    noire passe en mode selection et Windows MET LE PROGRAMME EN PAUSE a la ligne suivante, jusqu'a Echap ou Entree.
    Ne fait rien hors Windows ou sans console. Renvoie True si le mode a ete change."""
    if os.name != "nt":
        return False
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        entree = k32.GetStdHandle(-10)                  # STD_INPUT_HANDLE
        mode = ctypes.c_uint()
        if not k32.GetConsoleMode(entree, ctypes.byref(mode)):
            return False
        nouveau = (mode.value & ~0x0040) | 0x0080       # sans ENABLE_QUICK_EDIT_MODE, avec ENABLE_EXTENDED_FLAGS
        return bool(k32.SetConsoleMode(entree, nouveau))
    except Exception:
        return False
