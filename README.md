# Anyone Hear Me

*"Sesimi duyan var mı?"* ("Can anyone hear me?") is what people called out from under the rubble after the
6 February 2023 earthquakes in Türkiye. Their relatives posted calls for help on every channel they had:
voice notes, photos, tweets, WhatsApp messages. Volunteers sorted them by hand. The same building was
reported dozens of times, addresses were misheard or incomplete, and when one platform went dark the
requests on it were lost.

**Anyone Hear Me** is an agent that turns those scattered messages into verified, deduplicated rescue
tasks for coordinators, and works to settle what it cannot verify:

1. **Hear**: voice notes and live phone calls are transcribed with Muse Voice Transcribe, biased toward the
   place names of the affected district. During a call the agent reads the words as they come and tells
   the call-taker what is still missing ("Which Çağlayan? Onikişubat or Göksun?", "How many people?").
2. **Understand**: Muse Spark reads Turkish and Arabic text and attached photos and fills a structured
   record (location, people trapped, signs of life, needs, urgency) with the exact words each field came
   from. SAM 3.1 outlines collapsed buildings, rubble and people in the photos.
3. **Check the address**: the location is matched against the official neighbourhood list of the 11
   affected provinces; misheard names are corrected, names that exist in several places are flagged.
4. **Merge**: reports about the same building become one task. Rules catch the clear cases; for the rest
   Muse Spark judges "same building / different / unsure", and "unsure" is never merged. Conflicts
   ("5 people" vs "7 people") are shown with their sources, not averaged.
5. **Ask back**: when something does not add up (an address in several places, counts that disagree, no
   building named), the agent drafts a short question to the right reporter. A coordinator approves it;
   the agent reads the answer, settles the conflict with its source, and checks whether the task is now
   the same building as another one.
6. **Suggest teams**: the agent ranks the buildings that need a team now (signs of life, people, hours
   waited), holds back any whose place is not settled, and code pairs free teams with buildings to keep
   travel short. Every suggestion carries its reason.
7. **Human decision**: a coordinator approves every question and every team. The agent never dispatches on
   its own and never gives medical advice. Tasks export as CAP 1.2 alerts, the format emergency systems
   exchange.

An **open-weight mode** runs the same agent on Muse Spark Open, which can be self-hosted when cloud
services or platforms are unavailable; the demo lets you switch between the two and compare.

> Built during the [Meta Global AI Developer Hackathon](https://cerebralvalley.ai/e/meta-ai-developer-hackathon)
> (27 Oct – 4 Nov 2026). Everything in this repository was written during the event.
> All help requests used in demos and tests are **fictional**; no real person's data is used.

## Meta models used

| Model | Role |
|---|---|
| Muse Voice Transcribe | Voice notes and live calls to text (realtime API, place-name keyword bias) |
| Muse Spark | Reading reports (Turkish, Arabic, photos), same-building judgements, follow-up questions, reading answers, ranking buildings for teams |
| SAM 3.1 | Marks collapsed buildings, rubble and people in photos attached to requests |
| Muse Image | Generates the clearly labelled synthetic photos used in demos (no real disaster photos of real people) |
| Muse Spark Open | The same agent on self-hostable open weights (served by Baseten in the demo) |

## Setup

```bash
pip install -r requirements.txt
mkdir -p ~/.config/anyone-hear-me
echo "META_API_KEY=..." > ~/.config/anyone-hear-me/.env   # never commit keys
echo "BASETEN_API_KEY=..." >> ~/.config/anyone-hear-me/.env  # optional: live reports on Muse Spark Open
```

`ffmpeg` is needed for audio conversion.

## Try it

The web demo replays a recorded scenario (the first hours after the earthquake) through the agent, and
lets you take a live call, add your own report, answer the agent's questions as the reporter, and ask for
team suggestions:

```bash
uvicorn app.server:app --port 7860      # then open http://localhost:7860
```

or with Docker (the same image runs on Hugging Face Spaces):

```bash
docker build -t anyone-hear-me . && docker run -p 7860:7860 -e META_API_KEY=... anyone-hear-me
```

One voice note from the command line:

```bash
python scripts/voice_to_record.py note.m4a --province Kahramanmaraş
```

The replay (`data/demo/replay-meta.json`) is the agent's own recorded output for the evaluation scenario,
made with `scripts/build_replay.py` and `scripts/build_dispatch.py`; nothing in it is written by hand. In
the replay, the coordinator's approvals and the reporters' answers are simulated: Muse Spark plays each
reporter and answers only from the facts of the fictional incident (`ahm/simulate.py`).

## How well it works

Measured on the fictional scenario in `data/eval` (31 incidents, 129 messages, 113 of them help
requests), with the agent's own recorded runs. These are numbers on invented data written to be hard
(misspellings, numbers in words, names that exist in several towns, Arabic), not a field trial.

**Reading single reports** (`python scripts/evaluate.py`):

| | Muse Spark | Muse Spark Open |
|---|---|---|
| Help requests recognised | 113/113 | 113/113 |
| Address resolved correctly | 66/89 (74%) | 52/89 (58%) |
| Address flagged with the right options | 19/89 (21%) | 18/89 (20%) |
| Address wrong | 1/89 (1%) | 4/89 (4%) |
| People count as stated | 36/37 (97%) | 34/37 (92%) |
| "Some rescued, others still inside" read correctly | 6/6 | 1/6 |
| Evidence quotes found word for word in the message | 1,514/1,515 | 895/917 |

Every evidence quote is checked against the message it came from (`python scripts/check_quotes.py`), and the
demo shows the check under each report: a model can paraphrase, the record must not.

**Asking back** (`python scripts/eval_followups.py`): the same reports on the same desk, without and with
the follow-up loop (Muse Spark; answers from the simulated reporter):

| | Without | With follow-ups |
|---|---|---|
| Tasks (31 real incidents) | 43 | 37 |
| Incidents located correctly | 28/31 | 30/31 |
| Rescue incidents with the right people count | 10/25 | 23/25 |
| Rescue incidents with the right status | 19/25 | 21/25 |
| Open tasks with an unsettled issue | 19 | 3 |
| Merge precision (no wrong merges = 1.0) | 1.0 | 1.0 |
| Merge recall (duplicates found) | 0.80 | 0.92 |

59 questions were asked; 46 answers settled something. With follow-ups, Muse Spark Open reaches 22/25 right
counts and 2 unsettled tasks, but makes one wrong merge (precision 0.97) and leaves more duplicates
(recall 0.84, 44 tasks).

## Data

- `data/gazetteer.json`: districts and neighbourhoods of the 11 provinces affected on 6 Feb 2023, from
  [turkey-neighbourhoods](https://github.com/muratgozel/turkey-neighbourhoods) 4.0.3 (MIT, see
  `data/LICENSE.turkey-neighbourhoods`).
- `data/coords.json`: neighbourhood and district centres from [OpenStreetMap](https://www.openstreetmap.org/copyright)
  Nominatim (ODbL), fetched once by `scripts/geocode.py`. Where a neighbourhood is missing, the map shows
  the district centre with a dashed pin.
- `data/eval/`: the fictional scenario: 31 incidents and 129 messages in Turkish and Arabic, some with
  photos. Most messages were written by Muse Spark from incident descriptions; the Arabic and photo
  messages were added by hand.
- `data/demo/media/`: three synthetic photos made with Muse Image, and SAM 3.1's outlines of them. They
  are labelled as AI-generated wherever they appear.
- `data/demo/teams.json`: seven fictional search-and-rescue teams (base, size, equipment).
- `app/static/vendor/`: Leaflet 1.9.4 (BSD-2-Clause) and the Barlow, Barlow Condensed and Big Shoulders
  Stencil Display fonts (SIL Open Font License 1.1), served locally.

## Related work (what existed, and what this adds)

- **2023 volunteer work** (Açık Yazılım Ağı, Deprem Yapay Zeka on Hugging Face): OCR on screenshots,
  a Turkish NER model for addresses (deprem-ner), intent classifiers and satellite damage models, all fed
  into the afetharita map. Text and images only; repeated reports were not merged at run time.
  https://github.com/huggingface/blog/blob/main/using-ml-for-disasters.md · https://huggingface.co/deprem-ml
- **Research**: detecting help calls in earthquake tweets (arXiv 2302.13403) and classifying actionable
  requests with LLMs (arXiv 2504.16144). Neither merges duplicate reports, checks addresses or handles
  voice.

What this project adds: Turkish **voice notes**, **address checking against the official neighbourhood
list** (correct, flag or ask), **merging reports about the same building across channels with
conflicts shown**, a **human-approved task queue**, and an **open-weight offline mode**, measured on a
labelled, fictional evaluation set.

## Background

- Euronews (10 Feb 2023): how Twitter helped find people under the rubble, and how the platform block
  stopped rescue efforts that depended on it.
  https://tr.euronews.com/2023/02/10/kahramanmaras-depremi-twitter-enkaz-altinda-kalanlarin-bulunmasina-nasil-yardimci-oldu
- Açık Yazılım Ağı / afetharita: the volunteer effort that mapped help requests in 2023.
  https://github.com/acikyazilimagi/afet-org

## License

MIT
