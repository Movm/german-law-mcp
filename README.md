# German Law MCP

Ein selbst hostbarer, read-only MCP-Server für aktuelles deutsches Bundesrecht.
Der Server importiert einen datierten Snapshot aus
[QuantLaw/gesetze-im-internet](https://github.com/QuantLaw/gesetze-im-internet),
speichert die strukturierte Fassung in PostgreSQL und erzeugt daraus einen
kleinen SQLite-Leseindex für die MCP-Abfragen.

> **Kein Rechtsrat:** Die Ergebnisse können unvollständig, veraltet oder durch
> Parsingfehler verfälscht sein. Für verbindliche Fassungen immer die amtliche
> Veröffentlichung und qualifizierte Rechtsberatung heranziehen.

## Aktueller Umfang

Der Community-Server stellt acht read-only Tools bereit:

- `search_legislation` – Volltext- und Zitationssuche
- `get_provision` – einzelne Norm nach Gesetz/Artikel oder Ergebnis-ID
- `parse_citation` und `format_citation` – deutsche Zitate zerlegen/normalisieren
- `validate_citation` – Zitat gegen den importierten Bestand prüfen
- `check_currency` – Aktualität relativ zum importierten Snapshot einschätzen
- `list_sources` und `about` – Herkunft, Stand und Serverinformationen

Bewusst **nicht** enthalten sind derzeit Rechtsprechung, Gesetzesmaterialien,
historische Norm-Diffs, EU-Zuordnungen oder semantische Suche.

## Architektur und transparente Herkunft

```text
QuantLaw Snapshot (XML, datierter Git-Tag)
                  │
                  ▼
      moderner GII-XML-Importer
                  │
                  ▼
      PostgreSQL (kanonische Fassung)
                  │
                  ▼
     reproduzierbarer SQLite-Export
                  │
                  ▼
       MCP Streamable HTTP /mcp
```

PostgreSQL ist die lokale Datenwahrheit. SQLite ist nur ein wegwerfbares,
read-only Suchmodell für die fortgeführte Ansvar-MCP-Schicht. Diese Trennung
ermöglicht einen kleinen Runtime-Container, ohne Herkunft oder Struktur der
Daten zu verlieren.

**Qdrant, Embeddings und andere Vektordatenbanken werden in diesem Stand nicht
verwendet.** Eine spätere semantische Suche wäre ein optionaler Index und würde
PostgreSQL nicht als Quelle ersetzen.

Verwendete Vorarbeiten:

| Projekt | Verwendung | Lizenz |
| --- | --- | --- |
| [Ansvar-Systems/German-law-mcp](https://github.com/Ansvar-Systems/German-law-mcp) | MCP-Vertrag, Zitationslogik, SQLite-Leseschicht | Apache-2.0 |
| [nfelger/gesetze-aus-dem-internet](https://github.com/nfelger/gesetze-aus-dem-internet) | Vorbild für relationales Modell und GII-Parsing | Apache-2.0 |
| [QuantLaw/gesetze-im-internet](https://github.com/QuantLaw/gesetze-im-internet) | datierte Rohdaten-Snapshots | BSD-3-Clause für Repository-Code; Datenherkunft separat beachten |
| [QuantLaw/legal-data-preprocessing](https://github.com/QuantLaw/legal-data-preprocessing) | Vorbild für Snapshot-Verarbeitung | BSD-2-Clause |

Die konkreten Hinweise stehen zusätzlich in [NOTICE](NOTICE). Dieses Repository
enthält **keine fertig gebaute Gesetzesdatenbank**. Jeder Betreiber erzeugt den
Bestand reproduzierbar aus einem explizit gewählten Snapshot.

## Schnellstart mit Docker Compose

1. Einen existierenden `YYYY-MM-DD`-Tag im
   [QuantLaw-Repository](https://github.com/QuantLaw/gesetze-im-internet/tags)
   auswählen.
2. Konfiguration anlegen: `cp .env.example .env`
3. Mindestens diese Variablen setzen:

```dotenv
POSTGRES_PASSWORD=<langes-zufälliges-passwort>
QUANTLAW_SNAPSHOT=2026-07-16
MCP_BEARER_TOKEN=<langes-zufälliges-token>
ALLOWED_ORIGINS=https://claude.ai,https://chatgpt.com
```

Danach:

```bash
docker compose up --build
```

Der einmalige Import kann je nach Snapshot und Verbindung dauern. Anschließend:

- Health: `http://localhost:3000/health`
- MCP: `http://localhost:3000/mcp`

Neuen Snapshot einspielen:

```bash
docker compose run --rm ingest
docker compose run --rm export
docker compose restart mcp
```

## Zugriffsschutz

Wenn `MCP_BEARER_TOKEN` gesetzt ist, verlangt `/mcp`:

```http
Authorization: Bearer <token>
```

Browser-Anfragen mit `Origin` werden zusätzlich nur akzeptiert, wenn der
Ursprung in `ALLOWED_ORIGINS` steht. Ohne Bearer-Token ist der Server
ungeschützt; das ist ausschließlich für lokale Entwicklung gedacht.

Beispiel für Claude Code:

```bash
claude mcp add --transport http german-law https://recht.example.de/mcp \
  --header "Authorization: Bearer $GERMAN_LAW_MCP_TOKEN"
```

Claude.ai, ChatGPT und andere gehostete Connector-Oberflächen unterstützen
statische Bearer-Tokens nicht in jeder Konfiguration. Für mehrere Nutzer oder
öffentliche Connector-Registrierung ist OAuth 2.1 mit Protected Resource
Metadata der vorgesehene nächste Schritt. Der Bearer-Modus ist für den privaten
Grünerator und kontrollierte Clients gedacht.

## Coolify

- Ressource als Docker-Compose-Projekt aus diesem Repository anlegen
  (`/docker-compose.yaml`).
- Variablen aus `.env.example` in Coolify setzen; Secrets nicht committen.
- Eine persistente Domain auf Dienst `mcp`, Port `3000`, legen.
- Healthcheck: `/health`; MCP-Pfad: `/mcp`.
- PostgreSQL- und `mcp-data`-Volumes persistent halten.

Das Repository lädt beim ersten Lauf den gewählten QuantLaw-Tag. Ein Tag statt
`latest` macht Build und Datenstand prüfbar und wiederholbar.

## Lokale Entwicklung

Node.js 20+ und Python 3.13 werden unterstützt.

```bash
npm ci
npm run validate
python -m unittest discover -s pipeline/tests
```

Parser mit lokal vorhandenem QuantLaw-Datenzweig testen:

```bash
python -m pipeline.import_quantlaw \
  --repo-dir /path/to/gesetze-im-internet \
  --snapshot 2026-07-16 \
  --limit 10
```

## Daten- und Nutzungsbedingungen

- Amtliche Texte werden von
  [Gesetze im Internet](https://www.gesetze-im-internet.de/) veröffentlicht.
- Der Abruf erfolgt über das unabhängige QuantLaw-Archiv. Betreiber müssen die
  Nutzungsbedingungen, Quellenangaben, Abruflast und Rechte möglicher Anlagen
  selbst prüfen und einhalten.
- Das Projekt verspricht keine Vollständigkeit, Echtheit oder tagesaktuelle
  Rechtslage. Der importierte Snapshot wird in Dokument-Metadaten mitgeführt.
- Der Server ist für Recherche und technische Integration gedacht, nicht für
  automatisierte Rechtsentscheidungen ohne menschliche Prüfung.

## Lizenz

Servercode: [Apache License 2.0](LICENSE). Hinweise auf übernommene und
inspirierende Projekte: [NOTICE](NOTICE). Quelltexte und Datensätze behalten
ihre jeweiligen Rechte und Bedingungen; die Apache-2.0-Lizenz dieses Servers
beansprucht keine Rechte an den amtlichen oder archivierten Inhalten.
