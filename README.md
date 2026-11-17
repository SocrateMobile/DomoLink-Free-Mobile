# DomoLink Free Mobile

Intégration Home Assistant (HACS) pour **Free Mobile** (Espace Abonné `mobile.free.fr`), issue de l'analyse de l'application officielle `fr.free.moncomptefree`.

## Fonctionnalités
- Authentification sécurisée avec support **2FA OTP** (SMS ou bascule Email) et sauvegarde `trustUuid`.
- Rafraîchissement automatique du Bearer Token (`refreshToken`).
- Support **multi-lignes** automatique depuis `/account/api/subscriber/{login}`.
- **Capteurs** : Data nationale & roaming (Go), appels nationaux (min), SMS/MMS, hors-forfait (€), date de remise à zéro, forfait, dernière facture et impayés.
- **Capteurs binaires** : 5G, Avantage Freebox, alerte impayé, SIM active.
- **Interrupteurs** : activation/désactivation des options (`5G`, `5G SA`, `International`, `Voyage`, `Blocage numéros surtaxés`, `Data`, `Forfait bloqué`, `Notifications SMS`, `mVPN`).
