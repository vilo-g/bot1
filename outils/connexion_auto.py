#!/usr/bin/env python3
"""
connexion_auto.py : le bot ouvre la page du jeu dans SON Chrome, remplit pseudo + mot de passe
et clique sur « Se connecter », comme le ferait un humain.

Pourquoi passer par la page : la connexion contient un jeton reCAPTCHA invisible (RCT) que seule la
page du jeu sait produire. Le bot ne fabrique rien : il tape dans le formulaire, la page fait le reste.
Ensuite le bot retrouve le socket du jeu deja ouvert (comme test_envoi.py).

Mot de passe : JAMAIS ecrit dans un fichier ni affiche. Il est lu dans cet ordre :
  1. la variable d'environnement GGE_MDP ; 2. sinon il est demande a l'ecran (saisie masquee).
Pseudo : option --pseudo, ou variable GGE_PSEUDO, ou demande a l'ecran.

Exemples :
  py connexion_auto.py                      (demande pseudo et mot de passe)
  py connexion_auto.py --pseudo MonPseudo
  py connexion_auto.py --diagnostic         (ne tape rien : liste les champs de la page + capture d'ecran)

Si les champs ne sont pas trouves (formulaire dessine dans le jeu, pas en HTML), le script ecrit
captures/diagnostic_connexion.json et captures/diagnostic_connexion.png (sans aucun mot de passe) :
envoie-les moi, j'adapterai.

Installation : py -m pip install websockets
A utiliser avec ton propre compte. Les regles du jeu sur les automatismes restent a ta charge.
"""
import argparse
import asyncio
import getpass
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_envoi as T

CDP_HTTP = "http://127.0.0.1:9222"
URL_JEU = "https://empire.goodgamestudios.com/"

# ----------------------------------------------------------------------------- JavaScript execute dans la page
# Parcourt le document et les shadow roots ; ne renvoie JAMAIS la valeur d'un champ.
JS_OUTILS = r"""
(function () {
  const visible = e => !!(e.offsetParent || (e.getClientRects && e.getClientRects().length)) &&
                       getComputedStyle(e).visibility !== 'hidden';
  function tous(racine, sel, out) {
    out = out || [];
    racine.querySelectorAll(sel).forEach(e => out.push(e));
    racine.querySelectorAll('*').forEach(e => { if (e.shadowRoot) tous(e.shadowRoot, sel, out); });
    return out;
  }
  function champs() {
    const mdp = tous(document, 'input[type=password]').filter(visible);
    const textes = tous(document, 'input').filter(e =>
      visible(e) && ['text', 'email', ''].includes((e.getAttribute('type') || '').toLowerCase()));
    return {mdp: mdp[0] || null, textes: textes};
  }
  function choisirPseudo(c) {
    if (!c.mdp) return null;
    const re = /user|login|nick|name|mail|pseudo|identifiant|account/i;
    const par_nom = c.textes.find(e => re.test([e.name, e.id, e.placeholder, e.autocomplete].join(' ')));
    if (par_nom) return par_nom;
    // sinon : le dernier champ texte situe avant le champ mot de passe
    let choix = null;
    for (const e of c.textes) {
      if (e.compareDocumentPosition(c.mdp) & Node.DOCUMENT_POSITION_FOLLOWING) choix = e;
    }
    return choix || c.textes[0] || null;
  }
  function focaliser(e) {
    e.scrollIntoView({block: 'center'});
    e.focus();
    try { e.select(); } catch (x) {}
    return document.activeElement === e || (e.getRootNode().activeElement === e);
  }
  const API = {
    mode() {
      // 'connexion' si un bouton « Se connecter » est visible ; 'inscription' si seul « S'inscrire » l'est
      const txt = e => (e.innerText || e.value || '').trim();
      const bs = tous(document, 'button, input[type=submit], [role=button], a, div, span')
        .filter(e => visible(e) && e.children.length <= 1).map(txt);
      if (bs.some(t => /^se connecter$/i.test(t))) return 'connexion';
      if (bs.some(t => /^s'?inscrire$/i.test(t))) return 'inscription';
      return null;
    },
    vers_connexion() {
      const re = /^connexion\s*!?$/i;
      const e = tous(document, 'a, button, [role=button], span, div')
        .filter(x => visible(x) && x.children.length === 0 && re.test((x.innerText || '').trim()))[0];
      if (e) { e.click(); return true; }
      return false;
    },
    presents() {
      const c = champs();
      return {mdp: !!c.mdp, pseudo: !!choisirPseudo(c), mode: API.mode()};
    },
    serveur(voulu) {
      // choisit « France » dans une liste deroulante HTML si elle existe ; renvoie ce qui est affiche
      const out = [];
      tous(document, 'select').filter(visible).forEach(sel => {
        const txt = o => (o.text || '').trim();
        const opt = Array.from(sel.options).find(o => new RegExp(voulu, 'i').test(txt(o)));
        if (opt && sel.selectedOptions[0] !== opt) {
          const set = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value').set;
          set.call(sel, opt.value);
          sel.dispatchEvent(new Event('input', {bubbles: true}));
          sel.dispatchEvent(new Event('change', {bubbles: true}));
        }
        out.push(sel.selectedOptions[0] ? txt(sel.selectedOptions[0]) : '?');
      });
      return out;
    },
    decocher_retenir() {
      const lab = tous(document, 'label, span, div').filter(x => visible(x) && x.children.length === 0 &&
        /retenir le mot de passe/i.test((x.innerText || '').trim()))[0];
      if (!lab) return null;
      let cb = (lab.control) || (lab.parentElement && lab.parentElement.querySelector('input[type=checkbox]'));
      if (cb) { if (cb.checked) { cb.click(); return 'decoche'; } return 'deja decoche'; }
      return 'case introuvable';
    },
    focus(quoi) {
      const c = champs();
      const e = quoi === 'mdp' ? c.mdp : choisirPseudo(c);
      return e ? focaliser(e) : false;
    },
    cookies() {
      const re = /^(tout accepter|accepter( tout| et continuer)?|accept all|accept|j'accepte|ok|agree|allow all)$/i;
      const b = tous(document, 'button, a, [role=button]').filter(visible)
        .find(e => re.test((e.innerText || '').trim()));
      if (b) { b.click(); return (b.innerText || '').trim(); }
      return null;
    },
    bouton() {
      const c = champs();
      if (!c.mdp) return null;
      const re = /^(se connecter|connexion|log ?in|sign ?in)$/i;
      const f = c.mdp.form;
      let cand = f ? Array.from(f.querySelectorAll('button, input[type=submit], [role=button]')) : [];
      if (!cand.length) cand = tous(document, 'button, input[type=submit], [role=button]');
      cand = cand.filter(visible).filter(e => !/inscri|register|sign ?up/i.test((e.innerText || e.value || '')));
      const b = cand.find(e => re.test((e.innerText || e.value || '').trim()));
      if (b) { b.click(); return (b.innerText || b.value || 'submit').trim().slice(0, 30); }
      return null;
    },
    diagnostic() {
      const d = (e) => ({tag: e.tagName.toLowerCase(), type: e.getAttribute('type'), name: e.name || null,
                         id: e.id || null, placeholder: e.placeholder || null, visible: visible(e),
                         texte: (e.innerText || '').trim().slice(0, 40) || null});
      return {
        url: location.origin + location.pathname,
        champs: tous(document, 'input, textarea').map(d),
        boutons: tous(document, 'button, [role=button], input[type=submit]').slice(0, 40).map(d),
        iframes: tous(document, 'iframe').map(e => (e.src || '').split('?')[0]),
        canvas: tous(document, 'canvas').length
      };
    }
  };
  return API;
})()
"""


JS_SONDE = r"""
(function () {
  if (!document.querySelector('canvas')) return 'pas de canvas';
  if (window.__sonde) return 'deja';
  window.__sonde = [];
  const rc = e => { const r = e.getBoundingClientRect(); return [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)]; };
  const vue = () => ({fen: [innerWidth, innerHeight], dpr: devicePixelRatio, defil: [scrollX, scrollY],
                      canvas: Array.from(document.querySelectorAll('canvas')).map(c => rc(c).concat([c.width, c.height]))});
  addEventListener('mousedown', e => window.__sonde.push({t: 'clic', x: e.clientX, y: e.clientY,
      cible: e.target.tagName.toLowerCase() + (e.target.id ? '#' + e.target.id : ''), vue: vue()}), true);
  addEventListener('focusin', e => { const t = e.target;
      window.__sonde.push({t: 'focus', id: t.id || null, tag: t.tagName.toLowerCase(), type: t.type || null,
                           rect: t.getBoundingClientRect ? rc(t) : null}); }, true);
  addEventListener('input', e => { const t = e.target;      // jamais la valeur : seulement la longueur
      window.__sonde.push({t: 'saisie', id: t.id || null, longueur: (t.value || '').length}); }, true);
  return 'ok';
})()
"""


async def sonder(nav, dossier, duree=150, sortie=print):
    """Observe tes clics et les champs qui prennent le focus dans le jeu (aucune valeur saisie n'est enregistree)."""
    page = await session_page(nav)
    if page:
        z = await appliquer_taille(nav, page)
        sortie("Page forcee a %dx%d (resolution native du jeu).%s" % (TAILLE + (msg_zoom(z),)))
        await asyncio.sleep(2)
    installes = []
    for s, cid in await contextes(nav):
        p = {"expression": JS_SONDE, "returnByValue": True}
        if cid is not None:
            p["contextId"] = cid
        try:
            r = (await nav.call("Runtime.evaluate", p, s, timeout=8)).get("result", {}).get("value")
        except (T.ErreurCDP, asyncio.TimeoutError):
            continue
        if r in ("ok", "deja"):
            installes.append((s, cid))
    if not installes:
        raise T.ErreurCDP("Je n'ai pas trouve la zone du jeu (canvas) : ouvre d'abord la page du jeu.")
    sortie("Sonde installee. Pendant %d s, fais LENTEMENT dans la page du jeu, dans cet ordre :" % duree)
    sortie("  1) clique sur « Connexion ! »  2) clique dans « Nom ou e-mail »  3) clique dans « Mot de passe »")
    sortie("  4) clique sur la case « Retenir le mot de passe »  5) clique sur la liste « Choisir un monde / France »")
    sortie("  6) clique sur « SE CONNECTER » SEULEMENT si tu veux (tu peux taper un faux pseudo : rien n'est enregistre).")
    tout = []
    os.makedirs(dossier, exist_ok=True)
    chemin = os.path.join(dossier, "sonde_connexion.json")

    def ecrire():
        with open(chemin, "w", encoding="utf-8") as f:
            json.dump(tout, f, ensure_ascii=False, indent=1)
    ecrire()
    sortie("Fichier : %s (mis a jour a chaque clic)" % chemin)
    fin = time.time() + duree
    while time.time() < fin:
        for s, cid in installes:
            p = {"expression": "(()=>{const a=window.__sonde||[];window.__sonde=[];return a;})()", "returnByValue": True}
            if cid is not None:
                p["contextId"] = cid
            try:
                r = (await nav.call("Runtime.evaluate", p, s, timeout=8)).get("result", {}).get("value") or []
            except (T.ErreurCDP, asyncio.TimeoutError):
                continue
            for ev in r:
                tout.append(ev)
                ecrire()
                if ev["t"] == "clic":
                    sortie("  clic en (%s, %s) sur %s" % (ev["x"], ev["y"], ev["cible"]))
                elif ev["t"] == "focus":
                    sortie("  champ actif : %s %s #%s" % (ev["tag"], ev.get("type") or "", ev.get("id")))
        await asyncio.sleep(0.5)
    ecrire()
    sortie("Sonde terminee : %s (%d evenements)" % (chemin, len(tout)))


# ----------------------------------------------------------------------------- Chrome
def trouver_chrome():
    for p in (r"C:\Program Files\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
              os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe")):
        if os.path.exists(p):
            return p
    return shutil.which("chrome") or shutil.which("google-chrome")


def chrome_joignable():
    try:
        return json.load(urllib.request.urlopen(CDP_HTTP + "/json/version", timeout=2))
    except Exception:
        return None


def lancer_chrome(url, profil, sortie=print):
    """Ouvre le Chrome reserve au bot (meme profil que lancer_chrome.bat) s'il n'est pas deja ouvert."""
    info = chrome_joignable()
    if info:
        return info
    exe = trouver_chrome()
    if not exe:
        raise T.ErreurCDP("Chrome est introuvable : lance outils/lancer_chrome.bat puis relance ce script.")
    sortie("Ouverture de Chrome (profil du bot : %s)..." % profil)
    subprocess.Popen([exe, "--remote-debugging-port=9222", "--user-data-dir=" + profil, url])
    for _ in range(60):
        info = chrome_joignable()
        if info:
            return info
        time.sleep(0.5)
    raise T.ErreurCDP("Chrome ne repond pas sur le port 9222 (ferme les autres fenetres Chrome et recommence).")


# ----------------------------------------------------------------------------- pilotage de la page
async def contextes(nav):
    """[(session, id de contexte)] pour la page, ses iframes et leurs sessions."""
    out = []
    for s in await nav.sessions():
        if nav.infos.get(s, ("",))[0] not in ("page", "iframe"):
            continue
        ctx = nav.contextes.get(s) or {}
        ids = [cid for cid, (o, n, d) in ctx.items() if d] or [None]
        out += [(s, cid) for cid in ids]
    return out


async def js(nav, s, cid, appel):
    """Appelle une methode de JS_OUTILS dans un contexte ; renvoie None si le contexte est mort."""
    p = {"expression": "(%s).%s" % (JS_OUTILS, appel), "returnByValue": True}
    if cid is not None:
        p["contextId"] = cid
    try:
        r = await nav.call("Runtime.evaluate", p, s, timeout=8)
    except (T.ErreurCDP, asyncio.TimeoutError):
        return None
    return r.get("result", {}).get("value")


# Monde (serveur) choisi par la page : la page du jeu le retient dans son stockage local (cle ci-dessous, champ instanceId).
# Sur un Chrome neuf, elle peut proposer un autre monde que France : le bot le regle AVANT de taper les identifiants.
HOTE_JEU = "empire.goodgamestudios.com"
CLE_DONNEES_JEU = "ggs.gameData_12_1_nc"
INSTANCES_CONNUES = {"-fr1-": 3}           # France (zone EmpireEx_3) : lu sur une vraie session
FICHIER_MONDE = "monde.json"               # numero du monde appris sur une connexion reussie (remplace la valeur ci-dessus)
ATTENTE_LLI = 8                            # secondes : apres Entree, le jeu doit avoir envoye sa demande de connexion

TAILLE = (1920, 880)            # taille de page IMPOSEE = resolution native du jeu (canvas 1920x878)
FICHIER_CALIB = "calibrage_connexion.json"


async def appliquer_taille(nav, page, taille=TAILLE):
    """Force la page a 1920x880 (zoom 1) : c'est la resolution native du jeu, donc la mise en page est la meme
    sur tous les PC, quelle que soit la taille de l'ecran ou le zoom Windows. Si ta fenetre est plus petite,
    seule la partie visible change : les positions de clic restent valables.
    Zoom de Chrome (loupe, Ctrl+molette) : il agrandit/reduit la page, donc le jeu verrait une autre taille
    (ex. 67 % -> 2880x1320) et les champs ne seraient plus au meme endroit. Le bot ne peut pas remettre le zoom
    a 100 % lui-meme : il le mesure et le compense, pour que le jeu voie toujours 1920x880. Renvoie le zoom."""
    await nav.call("Emulation.setDeviceMetricsOverride",
                   {"width": taille[0], "height": taille[1], "deviceScaleFactor": 1, "mobile": False}, page)
    try:
        r = await nav.call("Runtime.evaluate", {"expression": "devicePixelRatio", "returnByValue": True}, page, timeout=5)
        zoom = float(r.get("result", {}).get("value") or 1)
    except Exception:
        return 1.0
    if not 0.2 <= zoom <= 6 or abs(zoom - 1) < 0.01:
        return 1.0
    await nav.call("Emulation.setDeviceMetricsOverride",
                   {"width": round(taille[0] * zoom), "height": round(taille[1] * zoom),
                    "deviceScaleFactor": 1 / zoom, "mobile": False}, page)
    return zoom


def msg_zoom(zoom):
    if abs(zoom - 1) < 0.01:
        return ""
    return " Zoom de Chrome a %d %% compense (pas besoin de le remettre a 100 %%)." % round(zoom * 100)


async def clic(nav, page, x, y):
    for t in ("mouseMoved", "mousePressed", "mouseReleased"):
        await nav.call("Input.dispatchMouseEvent",
                       {"type": t, "x": x, "y": y, "button": "none" if t == "mouseMoved" else "left",
                        "clickCount": 0 if t == "mouseMoved" else 1}, page)
        await asyncio.sleep(0.06)


JS_ACTIF = "(document.activeElement && document.activeElement.id) || null"
JS_FOCUS_SECOURS = """
(function (type) {
  const l = Array.from(document.querySelectorAll('input')).filter(e => /^txt_input_\\d+$/.test(e.id) && e.type === type);
  l.sort((a, b) => parseInt(a.id.slice(10)) - parseInt(b.id.slice(10)));
  const e = l[l.length - 1];
  if (!e) return null;
  e.focus();
  return e.id;
})
"""


async def ev(nav, s, cid, expr):
    p = {"expression": expr, "returnByValue": True}
    if cid is not None:
        p["contextId"] = cid
    try:
        return (await nav.call("Runtime.evaluate", p, s, timeout=5)).get("result", {}).get("value")
    except (T.ErreurCDP, asyncio.TimeoutError):
        return None


async def trouver_zone_jeu(nav, attente=60):
    fin = time.time() + attente
    while time.time() < fin:
        for s, cid in await contextes(nav):
            if await ev(nav, s, cid, "!!document.querySelector('canvas')"):
                return s, cid
        await asyncio.sleep(1.5)
    return None


async def attendre_jeu_charge(nav, sortie, delai=180):
    """Attend que le jeu ait fini de charger (ses champs de saisie existent) ; renvoie (session, contexte)."""
    fin = time.time() + delai
    dernier = 0
    while time.time() < fin:
        zone = await trouver_zone_jeu(nav, attente=6)
        if zone:
            n = await ev(nav, zone[0], zone[1], "document.querySelectorAll('input[id^=txt_input_]').length")
            if n and n >= 4:
                return zone
        if time.time() - dernier > 15:
            dernier = time.time()
            sortie("   chargement du jeu en cours...")
        await asyncio.sleep(2)
    raise T.ErreurCDP("le jeu n'a pas fini de charger en %d s (connexion Internet lente ?)" % delai)


def lire_calibrage(dossier):
    try:
        with open(os.path.join(dossier, FICHIER_CALIB), encoding="utf-8") as f:
            c = json.load(f)
        if tuple(c.get("taille", ())) == TAILLE and all(c.get(k) for k in ("connexion", "pseudo", "mdp")):
            return c
    except (OSError, ValueError):
        pass
    return None


JS_ID_ACTIF = """(() => { const a = document.activeElement;
  return (a && /^txt_input_\\d+$/.test(a.id)) ? [a.id, a.type] : null; })()"""


def calib_defaut():
    """Positions relevees sur une vraie capture de la page a 1920x880 (colonne centrale)."""
    return {"taille": list(TAILLE), "connexion": [960, 734], "pseudo": [960, 351, None], "mdp": [960, 445, None]}


class ErreurCalibrage(T.ErreurCDP):
    """Les positions enregistrees ne correspondent plus a l'ecran : il faut recalibrer."""


async def test_clic(nav, page, s, cid, x, y):
    """Clique en (x, y) et renvoie [id, type] du champ cache qui prend le focus (ou None)."""
    await ev(nav, s, cid, "document.activeElement && document.activeElement.blur && document.activeElement.blur()")
    await clic(nav, page, x, y)
    await asyncio.sleep(0.3)
    return await ev(nav, s, cid, JS_ID_ACTIF)


async def balayer(nav, page, s, cid, x, y0, y1, pas, sortie):
    """Clique de haut en bas sur la colonne x ; renvoie {id: [type, ymin, ymax]}."""
    champs = {}
    dernier = None
    debut = time.time()
    for y in range(y0, y1, pas):
        if time.time() - debut > 120:
            raise T.ErreurCDP("le balayage est trop lent (plus de 2 minutes) : la page repond mal")
        # on s'arrete des que les deux champs sont trouves et depasses : on ne passe pas sur « Connexion ! »
        if dernier is not None and {c[0] for c in champs.values()} >= {"text", "password"} and y > dernier + 30:
            break
        if (y // pas) % 10 == 0:
            sortie("   balayage y=%d (%d s)" % (y, time.time() - debut))
        r = await test_clic(nav, page, s, cid, x, y)
        if r:
            c = champs.setdefault(r[0], [r[1], y, y])
            c[2] = y
            dernier = y
    return champs


def numero(id_champ):
    return int(id_champ.split("_")[-1])


async def calibrer(nav, page, dossier, sortie=print):
    """Trouve TOUT SEUL ou cliquer (page a taille fixe) : balaye la colonne centrale et regarde quel champ cache
    prend le focus. Le resultat vaut pour tous les ordinateurs, puisque la taille de page est imposee."""
    await appliquer_taille(nav, page)
    s, cid = await attendre_jeu_charge(nav, sortie)
    await asyncio.sleep(3)
    connus = {t["targetId"] for t in (await nav.call("Target.getTargets")).get("targetInfos", [])}
    await capture_page(nav, page, os.path.join(dossier, "etat_page_calibrage.png"), sortie)
    X = TAILLE[0] // 2
    sortie("Calibrage automatique (environ 1 minute) : le bot clique de haut en bas dans la page, ne touche a rien.")
    champs = await balayer(nav, page, s, cid, X, 200, TAILLE[1] - 20, 10, sortie)
    tous = {}
    if not champs:
        await capture_page(nav, page, os.path.join(dossier, "etat_page_calibrage_fin.png"), sortie)
        raise T.ErreurCDP("aucun champ de saisie trouve au centre de la page : envoie-moi les captures "
                          "captures\\etat_page_calibrage*.png")
    ids = sorted(champs, key=numero)
    sortie("   champs trouves : %s" % ", ".join("%s (%s)" % (i, champs[i][0]) for i in ids))
    ex = max(numero(i) for i in ids)
    lien = None
    pseudo_id = next((i for i in sorted(champs, key=numero, reverse=True) if champs[i][0] == "text"), None)
    mdp_id = next((i for i in sorted(champs, key=numero, reverse=True) if champs[i][0] == "password"), None)
    if not pseudo_id or not mdp_id:
        raise T.ErreurCDP("champ pseudo ou mot de passe non reconnu")
    py = (champs[pseudo_id][1] + champs[pseudo_id][2]) // 2
    my = (champs[mdp_id][1] + champs[mdp_id][2]) // 2
    # le formulaire visible est-il deja celui de CONNEXION ? (ses champs portent les plus grands numeros)
    tous_ids = await ev(nav, s, cid, "Array.from(document.querySelectorAll('input')).map(e=>e.id).filter(i=>/^txt_input_\\d+$/.test(i))")
    hauts = sorted(tous_ids or [], key=numero)[-2:]
    if pseudo_id in hauts and mdp_id in hauts:
        sortie("   l'ecran affiche deja la CONNEXION.")
    else:
        sortie("   ecran d'inscription : recherche du lien « Connexion ! » sous le formulaire...")
        reg_pseudo = pseudo_id
        base = await test_clic(nav, page, s, cid, X, py)
        if not base or base[0] != reg_pseudo:
            raise T.ErreurCDP("l'ecran a change pendant le calibrage : relance avec --recalibrer")
        # Le lien est loin sous le formulaire (environ 3 fois l'ecart pseudo/mot de passe sous le mot de passe).
        # On ne balaie PAS toute la page : les liens « Conditions Generales » et « Politique de confidentialite »
        # s'y trouvent et ouvrent une fenetre qui bloque tout.
        ecart = max(my - py, 40)
        prevu = my + int(3.07 * ecart)
        essais = [prevu]
        for k in range(1, 6):
            essais += [prevu + 6 * k, prevu - 6 * k]
        for y in essais:
            if not 0 < y < TAILLE[1] - 5:
                continue
            await clic(nav, page, X, y)
            await asyncio.sleep(0.8)
            for t in (await nav.call("Target.getTargets")).get("targetInfos", []):
                if t["targetId"] not in connus and t.get("type") == "page":
                    try:
                        await nav.call("Target.closeTarget", {"targetId": t["targetId"]})
                    except T.ErreurCDP:
                        pass
            r = await test_clic(nav, page, s, cid, X, py)
            if r and r[0] != reg_pseudo and r[1] == "text":
                lien = [X, y]
                break
        if not lien:
            await capture_page(nav, page, os.path.join(dossier, "etat_page_calibrage_fin.png"), sortie)
            raise T.ErreurCDP("lien « Connexion ! » introuvable : envoie-moi captures\\etat_page_calibrage_fin.png")
        sortie("   lien « Connexion ! » trouve en (%d, %d)." % tuple(lien))
        # l'ecran de connexion place ses champs au meme endroit que l'inscription : 2 clics suffisent
        r1 = await test_clic(nav, page, s, cid, X, py)
        r2 = await test_clic(nav, page, s, cid, X, my)
        sortie("   champs de la connexion : %s / %s" % (r1[0] if r1 else None, r2[0] if r2 else None))
        if r1 and r2 and r1[1] == "text" and r2[1] == "password" and r1[0] != reg_pseudo:
            pseudo_id, mdp_id = r1[0], r2[0]
        else:
            sortie("   (positions differentes : nouveau balayage)")
            champs2 = await balayer(nav, page, s, cid, X, 120, TAILLE[1] - 20, 10, sortie)
            pseudo_id = next((i for i in sorted(champs2, key=numero, reverse=True) if champs2[i][0] == "text"), None)
            mdp_id = next((i for i in sorted(champs2, key=numero, reverse=True) if champs2[i][0] == "password"), None)
            if not pseudo_id or not mdp_id:
                raise T.ErreurCDP("champs de l'ecran de connexion non reconnus")
            py = (champs2[pseudo_id][1] + champs2[pseudo_id][2]) // 2
            my = (champs2[mdp_id][1] + champs2[mdp_id][2]) // 2
    calib = {"taille": list(TAILLE), "connexion": lien, "pseudo": [X, py, pseudo_id], "mdp": [X, my, mdp_id]}
    os.makedirs(dossier, exist_ok=True)
    with open(os.path.join(dossier, FICHIER_CALIB), "w", encoding="utf-8") as f:
        json.dump(calib, f)
    sortie("Calibrage enregistre : %s" % FICHIER_CALIB)
    return calib


class ErreurMonde(T.ErreurCDP):
    """Le jeu reste sur un autre monde que celui voulu : inutile de reessayer, il faut le choisir a la main une fois."""


def lire_instance(dossier, serveur):
    """Numero du monde voulu (ex. 3 pour '-fr1-') : appris sur une connexion reussie, sinon la valeur connue, sinon None."""
    try:
        with open(os.path.join(dossier or ".", FICHIER_MONDE), encoding="utf-8") as f:
            d = json.load(f)
        if d.get("serveur") == serveur and isinstance(d.get("instanceId"), int):
            return d["instanceId"]
    except (OSError, ValueError, AttributeError):
        pass
    return INSTANCES_CONNUES.get(serveur)


async def instance_page(nav, page):
    """Monde retenu par la page du jeu (instanceId), ou None."""
    expr = ("(()=>{try{const d=JSON.parse(localStorage.getItem(%s)||'null');return d&&Number.isInteger(d.instanceId)?d.instanceId:null}"
            "catch(e){return null}})()" % json.dumps(CLE_DONNEES_JEU))
    try:
        r = await nav.call("Runtime.evaluate", {"expression": expr, "returnByValue": True}, page, timeout=5)
        return r.get("result", {}).get("value")
    except T.ErreurCDP:
        return None


async def noter_instance(nav, page, dossier, serveur):
    """Apres une connexion reussie sur le bon serveur : retient le numero de ce monde (pour le regler plus tard si besoin)."""
    inst = await instance_page(nav, page)
    if not isinstance(inst, int) or not dossier:
        return
    try:
        os.makedirs(dossier, exist_ok=True)
        with open(os.path.join(dossier, FICHIER_MONDE), "w", encoding="utf-8") as f:
            json.dump({"serveur": serveur, "instanceId": inst}, f)
    except OSError:
        pass


def js_regler_monde(instance):
    """Script lance AVANT ceux du jeu au chargement de la page : met le monde voulu dans le stockage de la page
    (les autres donnees de la page sont gardees telles quelles)."""
    return ("(function(){try{if(location.hostname!==%s)return;const k=%s;let d=null;try{d=JSON.parse(localStorage.getItem(k)||'null')}"
            "catch(e){}if(!d||typeof d!=='object')d={};d.instanceId=%d;localStorage.setItem(k,JSON.stringify(d))}catch(e){}})();"
            % (json.dumps(HOTE_JEU), json.dumps(CLE_DONNEES_JEU), instance))


async def recharger_sur_monde(nav, page, instance):
    """Recharge la page du jeu en imposant le monde `instance` des le debut du chargement."""
    await nav.call("Page.enable", {}, page)      # sans lui, Chrome ignore le script ci-dessous
    ident = (await nav.call("Page.addScriptToEvaluateOnNewDocument", {"source": js_regler_monde(instance)}, page)).get("identifier")
    try:
        await nav.call("Page.reload", {"ignoreCache": False}, page)
        await asyncio.sleep(4)                   # le script a agi au tout debut du chargement
    finally:
        if ident:
            try:
                await nav.call("Page.removeScriptToEvaluateOnNewDocument", {"identifier": ident}, page)
            except T.ErreurCDP:
                pass


def monde_de(adr):
    """'wss://ep-live-de1-game.goodgamestudios.com/' -> 'de1' (pour les messages)."""
    import re
    m = re.search(r"-([a-z]{2,4}\d*)-game\.", adr or "")
    return m.group(1) if m else (adr or "?")


async def verifier_monde(nav, jeu, page, serveur, dossier, sortie):
    """AVANT de taper les identifiants : le socket de la page doit etre sur le bon serveur. Sinon, le monde voulu est
    regle dans la page, qui est rechargee (une fois). Renvoie l'adresse du socket (ou None s'il n'est pas trouve)."""
    if not serveur:
        return None
    try:
        adr = await asyncio.wait_for(jeu.connecter(), 40)
    except (T.ErreurCDP, asyncio.TimeoutError):
        return None
    if serveur in adr:
        return adr
    instance = lire_instance(dossier, serveur)
    if instance is None:
        raise ErreurMonde("ATTENTION : la page du jeu est sur le monde %s, pas sur %s. Dans la fenetre Chrome du bot, choisis le "
                          "bon monde dans la liste, puis relance le bot." % (monde_de(adr), serveur.strip("-")))
    sortie("ATTENTION : la page du jeu est sur le monde %s : je choisis le monde France (n %d) et je recharge la page."
           % (monde_de(adr), instance))
    await recharger_sur_monde(nav, page, instance)
    jeu.session = None
    await attendre_jeu_charge(nav, sortie)
    adr = None
    fin = time.time() + 30
    while time.time() < fin:                     # le socket du nouveau monde s'ouvre pendant le chargement
        try:
            adr = await asyncio.wait_for(jeu.connecter(), 10)
            if serveur in adr:
                sortie("   monde France choisi (%s)." % monde_de(adr))
                return adr
        except (T.ErreurCDP, asyncio.TimeoutError):
            jeu.session = None
        await asyncio.sleep(1)
    raise ErreurMonde("ATTENTION : la page du jeu reste sur le monde %s malgre le reglage. Dans la fenetre Chrome du bot, "
                      "choisis le monde France dans la liste, puis relance le bot." % monde_de(adr))


async def connexion_canevas(nav, jeu, page, pseudo, mdp, calib, sortie, delai_jeu, serveur=None, dossier=None):
    """Formulaire dessine dans le jeu : vrais clics (page a taille fixe, positions calibrees) + vraie frappe."""
    s, cid = await attendre_jeu_charge(nav, sortie)
    sortie("   jeu charge.")
    adr = await verifier_monde(nav, jeu, page, serveur, dossier, sortie)    # AVANT de taper quoi que ce soit
    if adr:
        s, cid = await attendre_jeu_charge(nav, sortie)      # (page rechargee si le monde a ete change)
    await asyncio.sleep(1)
    # le socket a deja ete accroche au debut (connexion) : on ne refait la recherche, longue, que s'il le faut
    accroche = jeu.session is not None
    if not accroche:
        try:
            await asyncio.wait_for(jeu.connecter(), 40)
            accroche = True
        except (T.ErreurCDP, asyncio.TimeoutError):
            pass
    sortie("   socket du jeu %s." % ("accroche" if accroche else "NON trouve (je continue quand meme)"))
    champs_dom = await ev(nav, s, cid, "Array.from(document.querySelectorAll('input')).filter(e => /^txt_input_\\d+$/.test(e.id)).map(e => [e.id, e.type])") or []
    calib = json.loads(json.dumps(calib))
    for cle, type_champ in (("pseudo", "text"), ("mdp", "password")):
        if calib[cle][2] is None:
            ids = sorted([i for i, t in champs_dom if t == type_champ], key=numero)
            if not ids:
                raise ErreurCalibrage("champs de saisie du jeu introuvables")
            calib[cle][2] = ids[-1]          # l'ecran de connexion porte les plus grands numeros
    x, y, id_pseudo = calib["pseudo"]
    r = await test_clic(nav, page, s, cid, x, y)
    sortie("   test du champ pseudo : %s" % (r[0] if r else "aucun champ actif"))
    if not r or r[0] != id_pseudo:                # on est encore sur l'inscription (ou le calibrage est obsolete)
        if not calib.get("connexion"):
            raise ErreurCalibrage("lien « Connexion ! » inconnu")
        lx, ly = calib["connexion"]
        sortie("   clic sur « Connexion ! » (%s, %s)" % (lx, ly))
        await clic(nav, page, lx, ly)
        await asyncio.sleep(1.5)
    for nom, (x, y, ident), valeur in (("pseudo", calib["pseudo"], pseudo), ("mot de passe", calib["mdp"], mdp)):
        r = await test_clic(nav, page, s, cid, x, y)
        if not r or r[0] != ident:
            raise ErreurCalibrage("le champ %s n'a pas pris le focus (attendu %s, obtenu %s)" % (nom, ident, r))
        await taper(nav, page, valeur)
        longueur = await ev(nav, s, cid, "document.getElementById(%s).value.length" % json.dumps(ident))
        sortie("   champ %s : %s caracteres saisis" % (nom, longueur))
        if longueur != len(valeur):
            raise T.ErreurCDP("la saisie dans le champ %s n'a pas ete prise en compte" % nom)
        await asyncio.sleep(0.5)
    await entree(nav, page)
    sortie("Validation envoyee (touche Entree).")
    debut = time.time()
    fin = debut + delai_jeu
    while time.time() < fin:
        if accroche and time.time() - debut > ATTENTE_LLI and jeu.session is not None:
            try:
                parti = await jeu.lli_envoye() or await jeu.est_connecte() or (await jeu.code_login()) is not None
            except T.ErreurCDP:
                parti = True                      # page en train de changer : on ne conclut rien
            if not parti:                         # le jeu n'a rien envoye : la validation n'a pas pris, inutile d'attendre
                raise T.ErreurCDP("la validation (touche Entree) n'a pas ete prise en compte par le jeu")
            accroche = False                      # demande partie : on attend la reponse du serveur
        try:
            if jeu.session is None:
                await asyncio.wait_for(jeu.connecter(), 40)
            code = await jeu.code_login()
            if code is not None and code != "0":
                raise T.ErreurCDP("Le serveur a refuse la connexion (code %s) : pseudo ou mot de passe incorrect ?" % code)
            if await jeu.est_connecte():
                return await jeu.connecter()
        except asyncio.TimeoutError:
            jeu.session = None
        except T.ErreurCDP as e:
            if "refuse" in str(e):
                raise
            jeu.session = None                   # contexte disparu : on recherchera le socket
        await asyncio.sleep(2)
    raise T.ErreurCDP("Pas de reponse du serveur a la connexion : la touche Entree n'a peut-etre pas valide. "
                      "Regarde la fenetre Chrome.")


async def session_page(nav):
    for s in await nav.sessions():
        if nav.infos.get(s, ("",))[0] == "page":
            return s
    return None


async def taper(nav, page, texte):
    """Frappe reelle (evenements de confiance) dans l'element qui a le focus."""
    await nav.call("Input.insertText", {"text": texte}, page)


async def entree(nav, page):
    base = {"key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13}
    await nav.call("Input.dispatchKeyEvent", dict(base, type="keyDown", text="\r"), page)
    await nav.call("Input.dispatchKeyEvent", dict(base, type="keyUp"), page)


async def capture_page(nav, page, chemin, sortie=print):
    """Ecrit une capture de ce que la page affiche VRAIMENT a 1920x880 (aucun mot de passe n'y figure)."""
    import base64
    try:
        r = await nav.call("Page.captureScreenshot", {"format": "png"}, page, timeout=15)
        os.makedirs(os.path.dirname(chemin), exist_ok=True)
        with open(chemin, "wb") as f:
            f.write(base64.b64decode(r["data"]))
        sortie("   capture de la page : %s" % chemin)
    except Exception as e:
        sortie("   (capture impossible : %s)" % e)


async def diagnostic(nav, dossier, sortie=print):
    page = await session_page(nav)
    rapport = []
    for s, cid in await contextes(nav):
        d = await js(nav, s, cid, "diagnostic()")
        if d:
            rapport.append(d)
    os.makedirs(dossier, exist_ok=True)
    chemin = os.path.join(dossier, "diagnostic_connexion.json")
    with open(chemin, "w", encoding="utf-8") as f:
        json.dump(rapport, f, ensure_ascii=False, indent=1)
    sortie("Diagnostic ecrit : %s" % chemin)
    if page:
        try:
            import base64
            r = await nav.call("Page.captureScreenshot", {"format": "png"}, page)
            png = os.path.join(dossier, "diagnostic_connexion.png")
            with open(png, "wb") as f:
                f.write(base64.b64decode(r["data"]))
            sortie("Capture d'ecran : %s" % png)
        except Exception as e:
            sortie("(capture d'ecran impossible : %s)" % e)


async def connexion(nav, jeu, pseudo, mdp, sortie=print, delai_formulaire=90, delai_jeu=150, dossier_diag=None,
                    serveur='-fr1-', calibrage=None, canevas=False, dossier_calib=None, recalibrer=False, recharger=False, deja_verifie=False):
    """Remplit le formulaire et attend que le socket du jeu soit ouvert. Renvoie l'adresse du socket."""
    page = await session_page(nav)
    if not page:
        raise T.ErreurCDP("Aucun onglet trouve dans le Chrome du bot.")
    await nav.call("Page.bringToFront", {}, page)

    # 1) deja connecte ? (socket du jeu deja ouvert)
    try:
        adr = await jeu.connecter()
        # un socket ouvert ne prouve rien : la page de login en ouvre un avant la connexion
        for i in range(1 if deja_verifie else 8):   # session deja ouverte ? (sinon on se connecte : plus rapide que d'attendre un nch)
            if await jeu.est_connecte():
                sortie("Deja connecte au jeu : %s" % adr)
                return adr
            if not deja_verifie:
                await asyncio.sleep(1)
        sortie("Socket ouvert mais pas encore de connexion (page de login) : je remplis le formulaire.")
    except T.ErreurCDP:
        pass

    # 1b) formulaire dessine dans le jeu : mode canevas (page a taille fixe + clics calibres)
    if canevas or calibrage:
        if recharger:
            sortie("Rechargement de la page du jeu (pour fermer toute fenetre restee ouverte)...")
            await nav.call("Page.reload", {"ignoreCache": False}, page)
            jeu.session = None
            await asyncio.sleep(2)               # ensuite : on attend que le jeu soit charge (verifie chaque seconde), pas plus
        if recalibrer:
            calibrage = await calibrer(nav, page, dossier_calib or dossier_diag or ".", sortie)
        elif not calibrage:
            calibrage = calib_defaut()
        z = await appliquer_taille(nav, page)
        sortie("Page forcee a %dx%d (meme mise en page sur tous les ordinateurs).%s" % (TAILLE + (msg_zoom(z),)))
        try:
            adr = await connexion_canevas(nav, jeu, page, pseudo, mdp, calibrage, sortie, delai_jeu, serveur, dossier_calib or dossier_diag)
        except ErreurCalibrage as e:
            sortie("Calibrage a refaire (%s) : je le refais automatiquement." % e)
            calibrage = await calibrer(nav, page, dossier_calib or dossier_diag or ".", sortie)
            await appliquer_taille(nav, page)
            adr = await connexion_canevas(nav, jeu, page, pseudo, mdp, calibrage, sortie, delai_jeu, serveur, dossier_calib or dossier_diag)
        sortie("CONNECTE : %s" % adr)
        if serveur and serveur not in adr:
            raise ErreurMonde("ATTENTION : connecte au monde %s au lieu de %s. Dans la fenetre Chrome du bot, deconnecte-toi, "
                              "choisis le monde France dans la liste, puis relance le bot." % (monde_de(adr), serveur.strip("-")))
        if serveur:
            await noter_instance(nav, page, dossier_calib or dossier_diag, serveur)
        return adr

    # 2) attendre le formulaire (bandeau de cookies compris)
    sortie("Recherche du formulaire de connexion (jusqu'a %d s)..." % delai_formulaire)
    cible = None
    derniere_bascule = 0
    fin = time.time() + delai_formulaire
    while time.time() < fin and not cible:
        for s, cid in await contextes(nav):
            c = await js(nav, s, cid, "cookies()")
            if c:
                sortie("   bandeau de cookies ferme (« %s »)" % c)
            p = await js(nav, s, cid, "presents()")
            if p and p.get("mode") == "inscription" and time.time() > derniere_bascule:
                if await js(nav, s, cid, "vers_connexion()"):
                    sortie("   page d'INSCRIPTION detectee : je passe sur « Connexion ! » (rien n'est rempli ici).")
                    derniere_bascule = time.time() + 5
                continue
            if p and p.get("mode") == "connexion" and p.get("mdp") and p.get("pseudo"):
                cible = (s, cid)
                break
        if not cible:
            await asyncio.sleep(1.5)
    if not cible:
        sortie("Formulaire introuvable en HTML (peut-etre dessine dans le jeu).")
        if dossier_diag:
            await diagnostic(nav, dossier_diag, sortie)
        raise T.ErreurCDP("formulaire de connexion introuvable")

    # 2b) serveur France + ne pas laisser le jeu memoriser le mot de passe dans le profil du bot
    choix = await js(nav, s, cid, "serveur('^France')")
    sortie("   listes deroulantes (valeur affichee) : %s" % (choix if choix else "aucune liste HTML trouvee"))
    r = await js(nav, s, cid, "decocher_retenir()")
    if r:
        sortie("   « Retenir le mot de passe » : %s" % r)

    # 3) remplir comme un humain : focus, frappe reelle, petite pause entre les champs
    s, cid = cible
    for quoi, valeur in (("pseudo", pseudo), ("mdp", mdp)):
        if not await js(nav, s, cid, "focus('%s')" % quoi):
            raise T.ErreurCDP("impossible de donner le focus au champ %s" % quoi)
        await taper(nav, page, valeur)
        await asyncio.sleep(0.6)
    sortie("Champs remplis (le mot de passe n'est ni affiche ni enregistre).")

    # 4) valider : touche Entree, puis clic sur le bouton si rien ne bouge
    await entree(nav, page)
    sortie("Validation envoyee (touche Entree).")
    fin = time.time() + delai_jeu
    essai_bouton = time.time() + 6
    while time.time() < fin:
        try:
            adr = await jeu.connecter()
            if await jeu.est_connecte():
                sortie("CONNECTE : %s" % adr)
                if serveur and serveur not in adr:
                    sortie("ATTENTION : ce n'est pas le serveur attendu (%s). Choisis le monde France dans la page." % serveur)
                return adr
        except T.ErreurCDP:
            pass
        if essai_bouton and time.time() > essai_bouton:
            essai_bouton = 0
            b = await js(nav, s, cid, "bouton()")
            sortie("   clic sur le bouton « %s »" % b if b else "   (pas de bouton trouve, j'attends encore)")
        await asyncio.sleep(2)
    raise T.ErreurCDP("Le socket du jeu ne s'est pas ouvert : identifiants refuses, ecran supplementaire "
                      "(choix du serveur, popup) ou page lente. Regarde la fenetre Chrome.")


# ----------------------------------------------------------------------------- lancement
def lire_args(argv=None):
    p = argparse.ArgumentParser(description="Connexion automatique au jeu (voir l'en-tete du fichier).")
    p.add_argument("--pseudo", default=os.environ.get("GGE_PSEUDO"))
    p.add_argument("--url", default=URL_JEU)
    p.add_argument("--diagnostic", action="store_true", help="ne tape rien : liste les champs + capture d'ecran")
    p.add_argument("--recalibrer", action="store_true", help="refait le calibrage automatique des clics")
    p.add_argument("--sonder", action="store_true", help="observe tes clics dans le jeu (pour apprendre au bot ou cliquer)")
    p.add_argument("--profil", default=None, help="dossier du profil Chrome (defaut : bot/profil-chrome)")
    return p.parse_args(argv)


async def principal(args):
    import websockets
    racine = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    profil = args.profil or os.path.join(racine, "profil-chrome")
    dossier_diag = os.path.join(racine, "captures")
    try:
        info = lancer_chrome(args.url, profil)
    except T.ErreurCDP as e:
        print("Erreur :", e)
        return
    pseudo = mdp = None
    if not args.diagnostic and not args.sonder:
        pseudo = args.pseudo or input("Pseudo : ").strip()
        mdp = os.environ.get("GGE_MDP") or getpass.getpass("Mot de passe (saisie masquee) : ")
        if not pseudo or not mdp:
            print("Pseudo ou mot de passe vide : arret.")
            return
    async with websockets.connect(info["webSocketDebuggerUrl"], max_size=None) as ws:
        nav = T.Navigateur(ws)
        await nav.demarrer()
        page = await session_page(nav)
        if page is None:
            r = await nav.call("Target.createTarget", {"url": args.url})
            await asyncio.sleep(3)
        else:
            # page vide ou autre site : on ouvre la page du jeu
            adresse = nav.infos.get(page, ("", ""))[1]
            if "goodgamestudios" not in adresse:
                await nav.call("Page.navigate", {"url": args.url}, page)
                await asyncio.sleep(4)
        jeu = T.Jeu(nav)
        try:
            if args.sonder:
                await sonder(nav, dossier_diag)
                return
            if args.diagnostic:
                await asyncio.sleep(6)
                await diagnostic(nav, dossier_diag)
                return
            await asyncio.wait_for(connexion(nav, jeu, pseudo, mdp, dossier_diag=dossier_diag, calibrage=None if args.recalibrer else lire_calibrage(dossier_diag), canevas=True, dossier_calib=dossier_diag, recalibrer=args.recalibrer, recharger=True), 420)
            z = await jeu.detecter_zone()
            print("Zone du serveur :", z or "non detectee (EmpireEx_3 par defaut)")
            print("Tu peux maintenant lancer test_envoi.py : le bot est connecte.")
        except asyncio.TimeoutError:
            print("Erreur : la connexion prend trop de temps (plus de 7 minutes) : arret.")
        except T.ErreurCDP as e:
            print("Erreur :", e)
        finally:
            pseudo = mdp = None


if __name__ == "__main__":
    try:
        asyncio.run(principal(lire_args()))
    except KeyboardInterrupt:
        print("\nArret.")
