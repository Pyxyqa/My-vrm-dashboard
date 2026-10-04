"""Generează hash-ul unei parole pentru secrets.toml.

Rulare:  python make_hash.py
"""
from getpass import getpass

from auth import hash_password

if __name__ == "__main__":
    p1 = getpass("Parolă nouă: ")
    p2 = getpass("Repetă parola: ")
    if p1 != p2:
        raise SystemExit("Parolele nu coincid.")
    if len(p1) < 8:
        raise SystemExit("Folosește cel puțin 8 caractere.")
    print("\npassword_hash = \"" + hash_password(p1) + "\"")
