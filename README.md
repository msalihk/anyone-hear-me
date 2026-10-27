# Anyone Hear Me

*"Sesimi duyan var mı?"* ("Can anyone hear me?") is what people called out from under the rubble after the
6 February 2023 earthquakes in Türkiye. Their relatives posted calls for help on every channel they had:
voice notes, photos, tweets, WhatsApp messages. Volunteers sorted them by hand. The same building was
reported dozens of times, addresses were misheard or incomplete, and when one platform went dark the
requests on it were lost.

**Anyone Hear Me** is an agent that turns those scattered messages into verified, deduplicated rescue
tasks for coordinators:

1. **Hear**: Turkish voice notes are transcribed with Muse Voice Transcribe, biased toward the place
   names of the affected district.
2. **Understand**: Muse Spark reads the text (and photos) and fills a structured record: location,
   people trapped, signs of life, needs, urgency, with the exact words each field came from.
3. **Check the address**: the location is matched against the official neighbourhood list of the
   11 affected provinces; misheard names are corrected, ambiguous ones are flagged with a question for
   the sender.
4. **Merge and verify** *(in progress)*: reports about the same building become one task; conflicts
   ("2 people" vs "4 people") are surfaced, not hidden.
5. **Human decision**: a coordinator approves every task. The agent never dispatches on its own and
   never gives medical advice.

An **offline mode** runs the same pipeline on Muse Spark Open, an open-weight model that can be
self-hosted when cloud services or platforms are unavailable.

> Built during the [Meta Global AI Developer Hackathon](https://cerebralvalley.ai/e/meta-ai-developer-hackathon)
> (27 Oct – 4 Nov 2026). Everything in this repository was written during the event.
> All help requests used in demos and tests are **fictional**; no real person's data is used.

## Meta models used

| Model | Role |
|---|---|
| Muse Voice Transcribe | Turkish voice notes to text (realtime API, place-name keyword bias) |
| Muse Spark | Structured extraction with evidence, image understanding, merge/verify agent |
| SAM 3.1 | Marks collapsed buildings, rubble and people in photos attached to requests |
| Muse Image | Generates the clearly labelled synthetic photos used in demos (no real disaster photos of real people) |
| Muse Spark Open | Offline mode on self-hostable open weights (tested on Baseten) |

## Setup

```bash
pip install -r requirements.txt
mkdir -p ~/.config/anyone-hear-me
echo "META_API_KEY=..." > ~/.config/anyone-hear-me/.env   # never commit keys
```

`ffmpeg` is needed for audio conversion.

## Try it

```bash
python scripts/voice_to_record.py note.m4a --province Kahramanmaraş
```

## Data

- `data/gazetteer.json`: districts and neighbourhoods of the 11 provinces affected on 6 Feb 2023, from
  [turkey-neighbourhoods](https://github.com/muratgozel/turkey-neighbourhoods) 4.0.3 (MIT, see
  `data/LICENSE.turkey-neighbourhoods`).

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
