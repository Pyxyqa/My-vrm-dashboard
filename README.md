# Dashboard fotovoltaic Victron VRM

Aplicație Streamlit care citește datele din VRM cu un token și afișează:

- **Ceasuri live**: producție PV, consum, rețea (import/export), baterie (încărcare/descărcare), SOC. Se reîmprospătează automat.
- **Indicatori**: energia de azi, din luna curentă, din anul curent și totalul.
- **Tabele și grafice** zilnice, lunare și anuale: producție, consum, import, export, baterie, autoconsum % și autonomie %. Fiecare tabel se poate descărca în CSV.
- **Autentificare** cu utilizator și parolă. Rolul *admin* are în plus tab-ul Diagnostic și butonul de reîncărcare a datelor. Conturile *oaspete* pot avea dată de expirare și pot fi limitate la anumite instalații.

## 1. Token VRM
În VRM mergi la **Preferences → Integrations → Access tokens → Add**. Copiază token-ul imediat, pentru că VRM nu ți-l mai arată a doua oară.

## 2. Parole
Pe calculatorul tău, cu Python 3 instalat, rulează în acest folder:
```
python make_hash.py
```
Scrii parola, iar scriptul îți afișează linia `password_hash = "..."`. Repetă pentru fiecare utilizator. Parola în clar nu se salvează nicăieri.

## 3. Publicare pe Streamlit Community Cloud (gratuit)
1. Creează un repository pe GitHub (poate fi privat) și urcă fișierele: `app.py`, `vrm.py`, `auth.py`, `make_hash.py`, `requirements.txt`, `.gitignore`. **Nu urca token-ul sau parolele.**
2. Pe https://share.streamlit.io, autentifică-te cu GitHub, apasă **Create app**, alege repository-ul și fișierul `app.py`.
3. În **Advanced settings → Secrets**, lipește conținutul din `secrets.toml.example`, completat cu token-ul și hash-urile tale. Apoi apasă **Deploy**.
4. În setările aplicației, la **Sharing**, las-o publică. Login-ul aplicației o protejează, iar oaspeții nu au nevoie de cont Streamlit.

## Gestionarea oaspeților
Totul se face din **Settings → Secrets**, iar modificările se aplică imediat:
- **Adaugi un oaspete**: creezi o nouă secțiune `[users.nume]` cu `role = "guest"`.
- **Îi limitezi accesul în timp**: adaugi `expires = "2026-12-31"`.
- **Îi retragi accesul**: ștergi secțiunea. Oaspetele este delogat la următoarea acțiune.

## Note
- Datele live sunt la fel de proaspete ca ultimul pachet primit de VRM de la GX. Intervalul se setează pe GX din **Settings → VRM online portal → Log interval**.
- Dacă un ceas arată „indisponibil” sau o valoare greșită, deschide tab-ul **Diagnostic** cu un cont de admin. Acolo vezi toate mărimile trimise de instalație și poți fixa codurile corecte în `[live_codes]`.
- Datele de energie sunt păstrate în cache 15 minute pentru anul curent și 24 de ore pentru anii trecuți, ca să nu încarce API-ul VRM.
- Pe planul gratuit, aplicația „adoarme” după câteva zile fără vizite. Se trezește cu un clic, în aproximativ 30 de secunde.

## Rulare locală
```
pip install -r requirements.txt
mkdir .streamlit && cp secrets.toml.example .streamlit/secrets.toml   # apoi completează-l
streamlit run app.py
```
