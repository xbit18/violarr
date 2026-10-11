# Funzionalità Torznab

Richiedi le funzionalità con:

```bash
curl 'http://localhost:8000/api?t=caps'
```

## Ricerche dichiarate

| Operazione | Parametri dichiarati                    |
| ---------- | --------------------------------------- |
| `search`   | `q`                                     |
| `movie`    | `q`, `imdbid`, `tmdbid`                 |
| `tvsearch` | `q`, `season`, `ep`, `imdbid`, `tmdbid` |

Tutte le richieste accettano anche `t`, `cat`, `limit`, `offset`, `apikey` ed
`extended`. `cat`, `apikey` ed `extended` sono accettati per compatibilità ma
non modificano la ricerca.

`limit` vale 100 per default e deve essere 1–200. `offset` vale 0 e non può
essere negativo. Gli IMDb ID numerici sono normalizzati aggiungendo `tt`.

## Ricerche complementari per ID esatto

Le ricerche film e TV eseguono tutte le strategie IMDb/TMDB fornite, non solo il
primo identificatore. Un metadato mancante (`NULL`) non è un conflitto; un
identificatore valorizzato ma incompatibile esclude la release per l'entità
confrontata. Le strategie per titolo e refusi conservativi operano insieme agli
ID, anche quando esistono corrispondenze esatte. TVDB non è supportato. La
ricerca generica e la navigazione senza testo restano invariate.

Le ricerche TV per stagione/episodio esaminano anche i file dello stesso
torrent. IMDb, stagione ed episodio devono appartenere alla stessa riga file,
oppure un TMDB corrispondente nel torrent identifica il contenuto mentre un file
fornisce stagione/episodio. IMDb mancante nel file è ammesso con TMDB nel
torrent, ma non identifica da solo un episodio IMDb. Conflitti noti tra
richiesta, torrent e file, anche tra file di serie diverse, escludono il
torrent. Evidenze valide nei file recuperano pacchetti stagionali anche con
episodio diverso nel torrent. Non vengono equiparate serie né rimappate
stagioni. Titolo, dimensione, categoria e magnet restituiti appartengono sempre
al torrent.

Per i pacchetti di film viene usato anche l'IMDb del singolo film in
`pack_files`, che può differire dall'identità della raccolta. Non esiste un TMDB
del film figlio: il TMDB del torrent non prova la corrispondenza di quel film.
La ricerca nei percorsi valida il nome del file, non le cartelle, ed esclude
extra, trailer, sottotitoli e file non video. Metadati e magnet rimangono quelli
del torrent.

SQL deduplica gli hash prima di ordinare per quattro livelli: IMDb/TMDB esatto
nel torrent, evidenza esatta nei file o nei film del pacchetto, titolo validato
ad alta confidenza, poi corrispondenza fuzzy conservativa. Un ramo TV
qualificato tramite file resta nel secondo livello anche se il TMDB appartiene
al torrent. Le corrispondenze sovrapposte vengono conservate, ma prevale il
livello più forte. Dentro ogni livello si ordina per seeder decrescenti (NULL in
fondo), poi hash crescente, prima di limite/offset. Vale anche senza titolo. I
preset mantengono finestre fisse di 1000 candidati, ranking locale e nessun
riempimento da finestre successive. Questi limiti vincolano le righe recuperate,
non il lavoro di aggregazione e ordinamento PostgreSQL: molte corrispondenze,
controlli sui file e offset elevati possono essere costosi. Gli indici storici
supportano ricerche esatte, ma piani di esecuzione e latenza su grandi dataset
richiedono misure sullo snapshot installato.

### Copertura conservativa e limiti

Il titolo accetta separatori di punteggiatura, per esempio
`Hanno ucciso l'Uomo Ragno - 883`. Per la TV il testo identifica la serie, non
il nome dell'episodio. Stagione ed episodio devono qualificare il torrent oppure
un singolo file; un'indicazione nel titolo da sola non prova l'episodio. Non si
combinano identità ed episodio da file diversi e si escludono serie miste.

Il recupero per titolo richiede al massimo 128 caratteri e 12 token, con almeno
un token significativo di tre caratteri; sole etichette di release non bastano.
Per titoli molto brevi usare gli ID. Il fuzzy richiede inoltre otto caratteri
alfanumerici e un token non numerico di quattro caratteri. `883` mantiene la
ricerca letterale senza espansione dei refusi. La similarità valida l'idoneità,
non ordina dentro un livello; i preset possono riordinare ogni finestra di 1000
righe.

Ogni fonte fuzzy usa un insieme indipendente dalla pagina, compatibile con gli
indici GIN esistenti. Oltre 2000 righe l'insieme viene omesso, mantenendo gli
abbinamenti esatti e letterali. Titoli generici possono quindi perdere risultati
fuzzy; il limite non vincola tutto il lavoro del database. Non vengono
modificati snapshot, schema o indici. Snapshot storici possono non offrire
`pack_files` o pg_trgm/indici necessari: un errore database sanificato non prova
l'assenza della release. Piani e latenza richiedono misure PostgreSQL
autorizzate separatamente.

## Categorie

| ID     | Nome     | Mappatura                          |
| ------ | -------- | ---------------------------------- |
| `2000` | Movies   | Tipo database `movie`              |
| `5000` | TV       | Altri tipi diversi da film e anime |
| `5070` | TV/Anime | Tipo database `anime`              |

## Risposta XML

Le ricerche restituiscono XML RSS 2.0. Ogni risultato può contenere titolo, GUID
info-hash non permalink, magnet link, `pubDate` UTC facoltativa, enclosure
BitTorrent e attributi Torznab `category`, `infohash`, `magneturl`, `seeders`,
`peers`, `size` facoltativo, `downloadvolumefactor=0`, `uploadvolumefactor=1` e
`description` facoltativa del provider.

Valori `t` sconosciuti producono un feed RSS vuoto valido. La serializzazione
XML esegue l'escaping dei valori. Con l'elaborazione attiva, la paginazione usa
[finestre fisse di 1000 righe](../how-it-works/result-processing).
