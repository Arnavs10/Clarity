# Sources

Every link below was verified on 12 August 2026.

## Grounding corpus (statutes)

Run `python tools/fetch_corpus.py` and five of these download themselves. If you hit
a 403 that is a network block, not a bad URL. Open it in a browser and save the PDF.

| Act | Used by | Link |
|---|---|---|
| Indian Contract Act 1872 | employment | https://www.indiacode.nic.in/bitstream/123456789/2187/2/A187209.pdf |
| Specific Relief Act 1963 | employment | https://www.indiacode.nic.in/bitstream/123456789/1583/7/A1963-47.pdf |
| Copyright Act 1957 | employment | https://www.indiacode.nic.in/bitstream/123456789/1367/1/A195714.pdf |
| Transfer of Property Act 1882 | rental | https://www.indiacode.nic.in/bitstream/123456789/2338/1/A1882-04.pdf |
| Registration Act 1908 | rental | https://www.indiacode.nic.in/bitstream/123456789/15937/1/the_registration_act%2C1908.pdf |
| Indian Stamp Act 1899 | rental | https://www.indiacode.nic.in/bitstream/123456789/15510/5/A1899-2%20.pdf |

All six are official bare Acts from India Code, the Government of India statute
repository. Public domain, no licence issue.

**One manual download.** The Model Tenancy Act 2021 is a model law circulated by the
Ministry of Housing and Urban Affairs, not a central Act, so it is not on India Code.
Get it from PRS Legislative Research, which hosts the full text plus a plain-language
summary that is useful in its own right:

https://prsindia.org/billtrack/the-model-tenancy-act-2021

Save it as `data/corpus/rental/model_tenancy_act_2021.pdf`.

## Contract documents to label

### Primary: SEC EDGAR, automated

```bash
python tools/fetch_documents.py --n 25
```

Public companies file executive employment agreements as EX-10 exhibits, so the full
text of thousands of them sits in the open on EDGAR. Free public JSON endpoint, no
account, no API key. The script pulls 25 in a few minutes and writes them straight
into `data/raw/`.

These are real negotiated contracts rather than templates, which matters: templates
are mild by construction and would leave you with almost no risky examples.

### Fallback: CUAD

If EDGAR is unreachable, CUAD is a straight download with no API involved.

| | |
|---|---|
| What | 510 real commercial contracts, 13,000+ clause labels written under lawyer supervision, 41 clause categories |
| Licence | CC BY 4.0, free for commercial and non-commercial use |
| Zenodo | https://zenodo.org/records/4595826 |
| Hugging Face | https://huggingface.co/datasets/theatticusproject/cuad |
| Paper | https://arxiv.org/abs/2103.06268 |

CUAD is weighted toward commercial agreements (distribution, licensing, supply)
rather than employment, so it is the backup, not the first choice. Its non-compete,
IP ownership, governing law, and termination categories still overlap your taxonomy.

If you use it, cite it. The attribution requirement is real and citing a NeurIPS
dataset in your README reads well anyway.

### Indian documents, for the eval set

| Source | Note |
|---|---|
| Your own past offer letters | Real, Indian, already in hand |
| Startup India non-compete and confidentiality template | https://www.startupindia.gov.in/content/dam/invest-india/Templates/public/Tools_templates/internal_templates/legal_templates/Non%20-%20Compete.doc |

## On US contracts and Indian grounding

EDGAR and CUAD are US documents. That is not a compromise, it is closer to the real
problem than an all-Indian corpus would be.

Indian tech companies routinely draft offer letters from US templates. That is
precisely why Indian offer letters are full of post-termination non-competes that are
void under section 27 of the Contract Act. The tool's job is to read US-style clause
language and judge it against Indian law, which is exactly what this corpus mix
trains and tests.

Keep a handful of genuinely Indian documents in the set so the eval is not purely US
language. Your own offer letters plus the Startup India template cover that.


## Redaction

Redact `data/raw/` before committing. Replace, do not delete, so clause structure
survives.

| Replace | With |
|---|---|
| Personal names | `[LESSOR]`, `[LESSEE]`, `[EMPLOYEE]` |
| Company names | `[COMPANY]` |
| Addresses | `[ADDRESS]` |
| Phone, email, PAN, Aadhaar | `[REDACTED]` |
| Salary figures | `[REDACTED]` |
| Rent and deposit figures | **Keep these.** The deposit-to-rent ratio is the risk |
