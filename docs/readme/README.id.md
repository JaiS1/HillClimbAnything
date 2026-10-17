Translated from README.md at commit 59b1150. The English README is authoritative for numbers.

<div align="center">

[English](../../README.md) · [简体中文](README.zh-CN.md) · [繁體中文](README.zh-TW.md) · [日本語](README.ja.md) · [한국어](README.ko.md) · [Español](README.es.md) · [Português (Brasil)](README.pt-BR.md) · [Français](README.fr.md) · [Deutsch](README.de.md) · [Русский](README.ru.md) · [Türkçe](README.tr.md) · [Tiếng Việt](README.vi.md) · **Bahasa Indonesia** · [हिन्दी](README.hi.md)

# HillClimbAnything

### Harness untuk hill-climb tantangan teknis apa pun.

**Arahkan coding agent Anda ke satu angka. Simpan hanya kemenangan yang nyata.**

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776ab?style=flat-square&logo=python&logoColor=white)](../../README.md#quick-start)
[![Zero dependencies](https://img.shields.io/badge/dependencies-0-2a78d6?style=flat-square)](../../pyproject.toml)
[![Tests](https://img.shields.io/badge/tests-355_passing-1baf7a?style=flat-square)](../../tests)
[![Agent skill](https://img.shields.io/badge/agent_skill-SKILL.md-5c4ee5?style=flat-square)](../../skills/hca/SKILL.md)
[![MIT license](https://img.shields.io/badge/license-MIT-f4c430?style=flat-square)](../../LICENSE)

<code>uvx --from hillclimbanything hca quick -- python bench.py</code>

<sub>Coba tanpa menginstal apa pun: seberapa berisik benchmark Anda, dan peningkatan apa yang bisa Anda deteksi?</sub>

<br /><br />

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../assets/climb-dark.svg" />
  <img src="../assets/climb-light.svg" alt="Waktu render Python-Markdown selama 18 percobaan: perubahan yang di-keep menurunkannya selangkah demi selangkah; empat confirm pada dokumen held-out lolos di -18.9, -45.9, -49.8 dan -53.8 persen; percobaan 9 terbaca -17.9 persen pada satu kali jalan tetapi -1.7 persen pada 20 blok, lalu di-revert" width="900" />
</picture>

</div>

---

Loop bergaya autoresearch memungkinkan agent mengedit, mengukur, dan menyimpan apa pun yang skornya terbaik. Pada metrik yang berisik, "terbaik" sering kali hanya keberuntungan. Pada desktop biasa, menyimpan skor terbaik mentah mempertahankan **17–55%** perubahan yang sebenarnya tidak berpengaruh apa-apa, tergantung benchmark-nya ([hasil pengukuran](../../spikes/phase0/RESULT.md)).

HillClimbAnything adalah **wasit** untuk loop itu:

- ia mengukur seberapa berisik angka Anda sebelum Anda mulai;
- ia hanya menyimpan perubahan jika melampaui noise tersebut;
- ia mengonfirmasi total peningkatan pada data yang tidak pernah dipakai agent untuk tuning.

Agent Anda mengusulkan, HCA yang memutuskan. HCA adalah CLI Python kecil tanpa dependensi, ditambah sebuah skill (Markdown biasa) yang mengajarkan metodenya kepada coding agent. Sudah diuji dengan Claude Code.

## Mulai cepat

**1. Instal** (Python 3.11+; diuji di Linux, WSL, dan Windows native, macOS belum diuji):

```bash
uv tool install hillclimbanything   # atau: pipx install hillclimbanything
hca setup                                                        # tambahkan skill agent (menampilkan rencananya lebih dulu)
```

`hca setup` menaruh skill di tempat agent mencarinya: `~/.claude/skills/hca` (Claude Code) dan
`~/.agents/skills/hca` (Codex, Cursor, Gemini CLI, Copilot). Apa pun yang digantikannya dicadangkan dulu,
dan `hca setup --uninstall` menghapusnya lagi. Tidak punya [`uv`](https://docs.astral.sh/uv/getting-started/installation/) atau [`pipx`](https://pipx.pypa.io/stable/installation/)? `pip install`
biasa berfungsi di dalam virtualenv; banyak sistem menolaknya di luar virtualenv (PEP 668).

<details>
<summary>Cara lain menambahkan skill</summary>

**Plugin Claude Code:**

```text
/plugin marketplace add JaiS1/HillClimbAnything
/plugin install hca@hillclimbanything
```

**[skills.sh](https://github.com/vercel-labs/skills):**

```bash
npx skills add JaiS1/HillClimbAnything --skill hca
```

Dengan cara mana pun, skill menjalankan CLI `hca`, jadi instal CLI-nya juga.
</details>

**2. Minta agent Anda:**

```text
Gunakan skill hca (HillClimbAnything) untuk membuat `python bench.py` lebih cepat: jalankan `hca quick`
lebih dulu, tulis hca.toml bersama saya, mendaki dengan `hca try`, dan laporkan hanya apa yang diloloskan oleh `hca confirm`.
```

**3. Atau jalankan sendiri:**

<div align="center">
<img src="../assets/demo-terminal.svg" alt="Terminal: hca quick melaporkan noise 0.6 persen; hca try men-keep perubahan -28.6 persen; percobaan lain di-revert meskipun satu kali jalan sempat terbaca -17.9 persen; hca confirm lolos di -53.8 persen" width="900" />
</div>

```bash
hca compare -- <A> -- <B>   # hasil pertama dalam hitungan menit untuk perintah yang cepat: apakah B benar-benar lebih baik dari A? tanpa git, tanpa hca.toml
hca quick -- <cmd>     # 2 menit, tanpa setup: seberapa berisik angka ini, kemenangan apa yang bisa kita deteksi?
hca init               # bekukan evaluator, buat branch, ukur noise baseline
hca try -m "why"       # ukur editan Anda terhadap keep terakhir → keep (commit) atau revert
hca confirm            # ukur ulang total peningkatan terhadap versi asli, pada data held-out
hca report             # setiap percobaan, verdict, dan buktinya
```

`hca compare` tidak butuh repo maupun konfigurasi: ia menjalankan kedua perintah dalam blok A/B teracak dan melaporkan selisihnya beserta interval dan nilai p, noise yang terlihat, dan selisih terkecil yang sebenarnya bisa ia deteksi, sehingga "tidak ada selisih yang terdeteksi" punya arti. Exit code 0 berarti B lebih baik dari A secara signifikan, 1 berarti tidak, 2 berarti ada run yang gagal atau tidak mengeluarkan metrik. Perintah dijalankan tanpa shell (tulis `sh -c '...'` jika perlu); metrik bawaannya adalah wall time, atau berikan `--metric NAME` untuk membaca baris `METRIC NAME=value`. Untuk pendakian terjaga pada kode Anda sendiri, langkah berikutnya adalah `hca init`. Biasanya ia menjalankan semua 20 blok (berhenti di 10 hanya bila pemeriksaan pertama sudah tegas). Exit code: 0 B lebih baik, 1 tidak, 2 tidak valid, 3 salah pakai, 4 error internal. Di Windows native, shell built-in dan shim `.cmd` seperti `npm` atau `dir` perlu `cmd /c ...`. Perintah A tidak boleh memuat `--`; pilih pemisah lain dengan `--sep TOKEN`.

Sebuah task terdiri dari brief, path yang boleh diedit agent, dan satu perintah yang mencetak angkanya:

```toml
# hca.toml
[task]
name     = "api-latency"
editable = ["src/**"]          # agent boleh mengubah ini
frozen   = ["bench/**"]        # evaluator: di-hash, tidak pernah diedit

[objective]
command       = ["python3", "bench/run.py"]   # mencetak METRIC p95_ms=212.4
metric        = "p95_ms"
direction     = "min"
useful_effect = "5%"           # kemenangan terkecil yang layak disimpan
```

## Showcase: Python-Markdown, 2× lebih cepat

Sebuah coding agent Sonnet mendaki [Python-Markdown](https://github.com/Python-Markdown/markdown) 3.11.1 dengan HCA sebagai wasit: 18 percobaan dalam sekitar 80 menit, 14 di-keep.

| Pemeriksaan | Hasil |
|---|---|
| `hca confirm` pada dokumen held-out | waktu render **−53.8%** (minimal −53.0%) |
| Pengukuran ulang independen di luar HCA, pada fixture pengujian proyek | **−63.5%** |
| Output: test suite proyek (1,126 test; 110 di-skip, sama seperti di 3.11.1) dan 23,000 dokumen acak | **identik** dengan 3.11.1 |
| Perubahan yang ditolak HCA tetapi akan di-keep oleh loop satu-kali-jalan | **3 dari 4**, salah satunya "−17.9%" yang sebenarnya hanya −1.7% |

Kemenangan terbesar datang dari melewati pola regex inline ketika teks tidak mungkin memuat karakter yang dibutuhkan setiap kecocokan. Baca [tulisan lengkap, patch, log per percobaan, dan ledger mentah](../../showcases/python-markdown/README.md). Ini satu mesin dan satu kali jalan, dan patch-nya belum ditinjau di upstream.

## Showcase: js-yaml, 15% lebih cepat

Pendakian kedua, dalam bahasa lain dan dengan jenis metrik yang berbeda: [js-yaml](https://github.com/nodeca/js-yaml) 5.5.0 di Node, mengukur `load()` sekali per dokumen pada 491 file nyata GitHub Actions, Kubernetes, Helm, Compose, dan OpenAPI. Sebuah coding agent melakukan 12 percobaan dalam sekitar satu jam, dan 3 di-keep.

<div align="center">
<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../assets/climb-yaml-dark.svg" />
  <img src="../assets/climb-yaml-light.svg" alt="Waktu `load()` js-yaml selama 12 percobaan: tiga perubahan yang di-keep menurunkannya bertahap; satu confirm pada dokumen held-out lolos di -15.3 persen" width="900" />
</picture>
</div>

| Pemeriksaan | Hasil |
|---|---|
| `hca confirm` pada dokumen held-out (genre yang sama, sebagian besar file berbeda) | waktu `load()` **−15.3%** median per item (minimal −13.3%) |
| A/B di clone bersih terhadap rilis asli, 200 proses baru | total waktu **−15.3%** (CI 95% −16.6% sampai −14.4%) |
| Output: 1,633 test milik js-yaml sendiri, 25,384 perbandingan untuk masing-masing build ESM dan CJS, 11,250 perbandingan fuzz | **0 perbedaan** dari 5.5.0 |

Peningkatannya berasal dari perubahan kecil yang menghindari kerja berlebih di parser: lebih sedikit alokasi per node, dispatch berdasarkan karakter pertama, dan jalur cepat untuk scalar biasa. Dua dari tiga percobaan yang di-keep menggabungkan perubahan yang masing-masing berada di bawah batas keep, dan tulisan lengkapnya menyatakan hal ini; confirm mencakup kondisi akhir. Metriknya adalah waktu per panggilan, cenderung dingin, pada dokumen yang sebagian besar kecil, bukan throughput di loop panas. Ini satu mesin, dan patch belum diajukan ke upstream. Baca [tulisan lengkap, patch, ledger, dan harness](../../showcases/js-yaml/README.md).

## Mengapa perlu wasit?

Dua build identik, dibandingkan dengan cara yang dipakai kebanyakan loop ([Phase 0](../../spikes/phase0/RESULT.md)):

| Aturan keep | Perubahan no-op yang di-keep | Percepatan 5% yang nyata dan di-keep |
|---|---|---|
| Simpan skor terbaik mentah | **17–55%** | 100% |
| Uji HCA pada blok acak | **≤ 1%** | 76–96% (15 blok) |

Rentang itu mencakup dua benchmark (web server dan CLI). Baris HCA adalah satu kali pengujian pada α = 1%; aturan yang dirilis lebih ketat.

Loop yang mempertahankan seperlima hingga separuh perubahan no-op-nya sedang mendaki noise. Peningkatannya terlihat menumpuk, lalu menguap saat Anda mengukur ulang.

## Cara kerjanya

```mermaid
flowchart LR
    Q["hca quick<br/>noise + detectable effect"] --> I["hca init<br/>freeze evaluator, baseline A/A"]
    I --> E["agent edits<br/>editable paths only"]
    E --> T{"hca try<br/>10–20 randomised blocks"}
    T -- "beats the noise<br/>and ≥ ½ useful effect" --> K["keep<br/>(commit)"]
    T -- "otherwise" --> R["revert<br/>(patch kept in ledger)"]
    K --> E
    R --> E
    K -. "every few keeps" .-> C{"hca confirm<br/>held-out, vs original"}
    C -- "pass" --> E
    C -- "fail" --> B["roll back to<br/>last confirmed"]
```

- **Blok acak.** Setiap percobaan menjalankan baseline dan kandidat berurutan dalam urutan acak, 10 hingga 20 kali. Urutan acak membuat uji sign-flip eksak tetap valid apa pun bentuk noise-nya: ekor berat, lonjakan termal, atau game yang berjalan di latar belakang.
- **Bukti dan besaran.** Sebuah keep memerlukan uji satu sisi yang lolos pada 1% per percobaan, dan estimasinya harus mencapai setidaknya setengah dari useful effect Anda.
- **Lengan yang identik.** Kedua lengan adalah git worktree bersih dari commit yang persis. Commit yang di-keep adalah tree yang persis sama dengan yang diukur.
- **Confirm, atau dianggap tidak terjadi.** `hca confirm` memeriksa riwayat, lalu mengukur ulang total peningkatan terhadap versi asli. Agent hanya melihat PASS atau FAIL.
- **Semuanya di disk.** Patch, run mentah, verdict, dan alasan setiap percobaan ada di `.hca/`.

## Mode eksplorasi (eksperimental, opsional)

Semua yang di atas adalah *mode garis*: setiap kandidat diuji terhadap keep terakhir. Mode eksplorasi menambahkan memori ide dan pohon program kandidat, tanpa menurunkan syarat untuk sebuah keep.

- **Ide.** Agent menyimpan hipotesisnya di `IDEAS.md` (`## I4 [avoid-work] Skip absent patterns`). `hca try --idea I4` mencatat ide mana yang diuji sebuah percobaan, dan `hca next` menampilkan hasil setiap ide.
- **Screen.** `hca try --explore` mengukur sebuah anak terhadap *induk*-nya, yang tidak harus keep terakhir, dalam 5–8 blok. Screen memberi peringkat pada anak dan mengarsipkannya. Screen tidak pernah bisa melakukan keep, dan bukan bukti.
- **Arsip.** Satu elite per modul yang disentuh, sehingga batu loncatan yang awalnya terlihat lebih buruk bertahan cukup lama untuk membaik. Setelah setiap screen, HCA mengambil sampel induk berikutnya (keep terakhir setidaknya 40% dari waktu) dan mengatur path yang bisa diedit ke induk itu.
- **Promosi.** `hca try --promote M` menguji anggota M terhadap keep terakhir sebagai percobaan biasa, dengan blok baru, sehingga tingkat keep palsu 1% per uji tetap berlaku bagaimanapun M dipilih. `hca confirm` bekerja seperti sebelumnya; confirm yang gagal menandai setiap anggota yang dibangun di atas keep yang dibatalkannya sebagai tainted.
- **`hca next`** menampilkan anggaran, induk dan garis keturunannya, ide, arsip, dan petunjuk promosi, paling banyak dalam 60 baris. Perintah ini menggantikan `hca status`, yang masih dipertahankan selama satu rilis. Jika dijalankan sendiri, `hca next` juga memilih induk dan mencetak perintah yang siap dijalankan; memilih induk lain memerlukan `--parent M --why "..."`, dan laporan menghitung override tersebut.

Ini belum terbukti. Apakah populasi kandidat mendaki lebih baik daripada satu garis pada target nyata, itulah yang akan diukur oleh pendakian pembuktian di [dokumen desain](../DESIGN-climb-engine.md). Sampai itu dijalankan, mode garis tetap menjadi default, dan kedua showcase di atas didaki dalam mode garis. Mode eksplorasi hanya bekerja dalam mode `blocks`. Screen yang tercatat keluar dengan kode 5, tidak pernah 0, agar tidak tertukar dengan keep; yang tidak valid keluar dengan 2.

## Apa yang bisa didaki

Apa pun yang dapat diubah oleh sebuah perintah menjadi angka, selama angkanya cukup berulang untuk diukur noise-nya.

| Target | Contoh metrik | Status |
|---|---|---|
| Hot path library | waktu render / parse | ✅ sudah ada showcase (Python-Markdown, −53.8%; js-yaml, −15.3%) |
| Waktu CLI dan build | detik wall-clock | 🧪 terkalibrasi ([Phase 0](../../spikes/phase0/RESULT.md)) |
| Latensi web server | time to first byte per route | 🧪 terkalibrasi ([Phase 0](../../spikes/phase0/RESULT.md)) |
| Pemuatan halaman di browser | LCP per halaman | 🗺️ showcase direncanakan |
| Frame time UI atau game | p99 frame ms per scene | 🗺️ showcase direncanakan |
| Akurasi model atau prompt | skor eval per item | 🧪 mode `items`, sudah di-unit-test |
| Training GPU berbayar | skor eval setelah training | 📋 metode campaign ada di [referensi skill](../../skills/hca/references/campaign-card.md); tooling direncanakan |

## Untuk agent

Jika Anda adalah AI agent yang membaca ini:

- **Metode:** [`skills/hca/SKILL.md`](../../skills/hca/SKILL.md). Mencakup pemeriksaan premis, satu mekanisme per percobaan, dan tidak pernah menyentuh file frozen.
- **Spec dan kontrak output:** [`skills/hca/references/spec.md`](../../skills/hca/references/spec.md).
- **Ringkasan untuk crawler dan asisten:** [`llms.txt`](../../llms.txt).
- **Mengerjakan repo ini:** [`AGENTS.md`](../../AGENTS.md).

| Exit code | `hca try` | `hca confirm` |
|---|---|---|
| 0 | di-keep (`keep`) | lolos |
| 1 | di-revert (`revert`) | gagal (di-rollback) |
| 2 | tidak valid / void / ditolak: tidak ada yang di-keep, editan Anda dibiarkan | tidak meyakinkan atau ditolak |
| 3 / 4 | kesalahan penggunaan / kesalahan internal | sama |

Laporkan hanya apa yang diloloskan `hca confirm`, beserta batasnya dan jumlah percobaan.

## FAQ

<details>
<summary><strong>Apakah HCA memanggil LLM atau memerlukan API key?</strong></summary>

Tidak. HCA tidak pernah memanggil model. Agent Anda (Claude Code, Codex, Cursor, atau yang lain) yang berpikir, sedangkan HCA hanya mengukur dan memutuskan. `hca quick` bekerja tanpa agent sama sekali.
</details>

<details>
<summary><strong>Berapa lama satu percobaan?</strong></summary>

Sekitar 20 hingga 40 kali menjalankan perintah Anda: 10 blok, diperpanjang menjadi 20 bila hasilnya menjanjikan, dengan dua run per blok. Benchmark 3 detik memakan 1 hingga 2 menit per percobaan. `hca quick` memberi tahu biayanya sebelum Anda mulai.
</details>

<details>
<summary><strong>Metrik saya berisik. Apakah ini akan berhasil?</strong></summary>

`hca quick` menjawabnya dalam sekitar 2 menit. Ia melaporkan noise dan kemenangan terkecil yang bisa Anda deteksi. Jika jawabannya "terlalu berisik di sini", ia menyebutkan perbaikan termurah: lebih banyak item per run, run yang lebih lama, atau menutup beban lain. Mendaki sebelum itu hanya membuang tenaga.
</details>

<details>
<summary><strong>Bisakah agent curang?</strong></summary>

Bisa mencoba, dan HCA membuatnya merepotkan:

- file evaluator yang frozen di-hash;
- edit di luar path yang dideklarasikan membatalkan percobaan;
- kedua lengan berjalan dari worktree bersih;
- crash dinilai sebagai nilai terburuk;
- percobaan berulang dihitung.

Ini bukan batas keamanan, jadi baca patch-nya. [docs/LIMITS.md](../LIMITS.md) mendaftar setiap celah yang diketahui dan cara menanganinya.
</details>

<details>
<summary><strong>Apa bedanya dengan autoresearch?</strong></summary>

[autoresearch](https://github.com/karpathy/autoresearch) menunjukkan betapa produktifnya agent di dalam loop, dan HCA mempertahankan loop itu. Yang ditambahkan HCA adalah keputusannya: untuk perintah dan metrik apa pun, apakah suatu perubahan itu nyata atau noise, ditambah langkah confirm yang melindungi dari winner's curse.
</details>

<details>
<summary><strong>Bagaimana dengan run yang mahal, seperti training GPU?</strong></summary>

Referensi skill membawa disiplin dari kampanye fine-tuning model sungguhan yang berlangsung berbulan-bulan:

- kartu yang di-preregister dengan kontrol "compute sama, tanpa ide" sebagai pembanding;
- gladi murah sebelum run berbayar;
- adversarial review;
- watcher dan meter anggaran di dalam job remote;
- evaluasi mendalam sebelum verdict apa pun.

Tooling untuk ini direncanakan.
</details>

<details>
<summary><strong>Windows?</strong></summary>

Windows native berfungsi (Python 3.11+). Timeout mematikan seluruh pohon proses lewat Job Object dan `taskkill`. Tidak ada shell, jadi tulis perintah sebagai daftar argv (file batch menjadi `["cmd", "/c", "build.bat"]`). WSL tetap memberi isolasi proses paling ketat, dan macOS belum diuji.
</details>

## Dokumentasi

- [Showcase: Python-Markdown](../../showcases/python-markdown/README.md)
- [Showcase: js-yaml](../../showcases/js-yaml/README.md)
- [Batasan dan celah yang diketahui](../LIMITS.md)
- [Kalibrasi Phase 0](../../spikes/phase0/RESULT.md)
- [Desain](../DESIGN-hca.md)
- [Kontrak arsitektur](../ARCHITECTURE-v1.md)

## Status dan kredit

Rilis riset awal (0.2.0.dev): loop inti dan dua showcase. HCA tumbuh dari [Dream Loop](https://github.com/JaiS1/dream-loop/tree/dream-loop-v0.1), yang statistik berpasangannya dipertahankan. HCA meminjam gagasan dari loop bergaya [autoresearch](https://github.com/karpathy/autoresearch) dan dari kebersihan evaluasi dalam makalah [Harness Learning](https://arxiv.org/abs/2609.35738).

```bash
python3 -m unittest discover -s tests      # pengembangan
```

[Lisensi MIT](../../LICENSE). Jika HCA menyelamatkan Anda dari merilis kemenangan palsu, sebuah bintang membantu orang lain menemukannya.
