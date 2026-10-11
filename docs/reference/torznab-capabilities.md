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
identificatore valorizzato ma incompatibile esclude la release. Senza ID resta
la ricerca per titolo esistente; una ricerca esatta vuota non viene ripetuta per
titolo. TVDB non è supportato.

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

SQL deduplica gli hash prima di ordinare per seeder decrescenti (NULL in fondo),
poi hash crescente, e applicare limite/offset. I preset mantengono finestre
fisse di 1000 candidati, ranking locale e nessun riempimento da finestre
successive. Questi limiti vincolano le righe recuperate, non il lavoro di
aggregazione e ordinamento PostgreSQL: molte corrispondenze, controlli sui file
e offset elevati possono essere costosi. Gli indici storici supportano ricerche
esatte, ma piani di esecuzione e latenza su grandi dataset richiedono misure
sullo snapshot installato.

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
