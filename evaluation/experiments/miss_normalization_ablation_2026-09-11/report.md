# Miss query normalization ablation

Production-only candidate-pool Top-30; reranking is disabled. Gold alternatives within one Group use OR semantics.

Groups: `18`; unique original queries: `9`.

| Variant | Top-30 hits | Recall |
|---|---:|---:|
| Original query | 0 | 0.0% |
| Symbol normalized | 0 | 0.0% |
| Fields + table headers | 0 | 0.0% |
| Fields + headers + standards | 0 | 0.0% |
| Restored context only | 9 | 50.0% |
| Targeted standard only | 5 | 27.8% |
| Restored context + targeted standard | 18 | 100.0% |

| Case | Evidence | Original | Symbols | Fields | +Standards | Context | Target | Both |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| transformer-01-b8b08b984c56:ac80eb810d942c15f95b76a3 | table | - | - | - | - | 4 | - | 3 |
| transformer-01-b8b08b984c56:21bec835a082bbd882f22417 | section | - | - | - | - | - | 16 | 16 |
| transformer-01-b8b08b984c56:388aab9c4e1df6cf06c298fc | table | - | - | - | - | 4 | - | 4 |
| transformer-02-6ea76155782d:79c42b06eff36c3d0fd8e3dd | table | - | - | - | - | 4 | - | 3 |
| transformer-02-6ea76155782d:18cb41ca7a3e3b0bcb1f300a | section | - | - | - | - | - | 16 | 16 |
| transformer-02-6ea76155782d:92ff2ae2fa8cd7e490806ec8 | table | - | - | - | - | 4 | - | 4 |
| transformer-03-85336b7ef54a:bf618110c432c6d41191840f | table | - | - | - | - | 4 | - | 3 |
| transformer-03-85336b7ef54a:83e346100e3a3702cd2b2076 | section | - | - | - | - | - | 16 | 16 |
| transformer-03-85336b7ef54a:4a1c9d767f5eecb641f8ecca | table | - | - | - | - | 4 | - | 4 |
| transformer-03-85336b7ef54a:ec4e690d16b7052273fab745 | table | - | - | - | - | 4 | - | 5 |
| transformer-04-5a18abe4dea2:e39f08ae1fd34bf9a3bfde0c | table | - | - | - | - | 4 | - | 3 |
| transformer-04-5a18abe4dea2:ebe63918517db5ae9fdfc3e7 | table | - | - | - | - | 4 | - | 4 |
| transformer-05-b86fc1992dfd:6498cbe7e8348a55415ca19b | table | - | - | - | - | - | - | 26 |
| transformer-05-b86fc1992dfd:3743755e2bfd2152f6c49105 | table | - | - | - | - | - | - | 19 |
| transformer-05-b86fc1992dfd:7f0060cfc798d675bf26024b | table | - | - | - | - | - | - | 26 |
| transformer-05-b86fc1992dfd:2b3fc260429b880b30d987d6 | table | - | - | - | - | - | - | 19 |
| transformer-06-0224e4ebc67b:f6178628ce27ee691ce3f477 | section | - | - | - | - | - | 16 | 16 |
| transformer-07-c1028ce20861:7c3441d4d89c5fabcbde0787 | section | - | - | - | - | - | 16 | 16 |

Variants add NFKC symbol normalization, explicit parameter/table-header fields, units, and declared standards. The final variant restores model/capacity/voltage from sibling report cases via legacy numeric signatures and routes the parameter family to one standard. None uses the Gold locator, table/section number, or Gold quote.
