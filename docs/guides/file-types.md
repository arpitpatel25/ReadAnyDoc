# File types

ReadAnyDoc reads PDFs (including password-protected ones), images, Office files, spreadsheets, text and email.
Some older formats need a converter installed on the machine; without it the error names the program and the
install command.

| Install | Unlocks |
|---|---|
| nothing | PDF, PNG, JPG, GIF, BMP, WEBP, TIFF, DOCX, XLSX, XLSM, CSV, PPTX, TXT, EML, MSG |
| LibreOffice (`brew install --cask libreoffice`, `apt install libreoffice`) | DOC, XLS, PPT, RTF, ODT, ODS, ODP |
| macOS `textutil` (built in) | DOC, RTF, ODT |
| `pillow-heif`, macOS `sips` or `heif-convert` | HEIC photos |

Each type was sent to Reducto and to ReadAnyDoc; "words in common" is the token-set similarity of all text.

| Type | Works | Against Reducto |
|---|---|---|
| PDF | yes | see [Parse](parse.md) |
| PNG, JPG, GIF, BMP, WEBP | yes | 94-97% words in common on the test page (two different readers) |
| TIFF, multi-page | yes | 96%; one page per frame |
| HEIC | yes, with a decoder on the machine | 97% (decoded by macOS `sips` in the test) |
| DOCX | yes, read natively | 100%, same 11 blocks, 2 pages, table, links, properties. Block boxes are estimates (no rendering) |
| XLSX / XLSM | yes, read natively | 100%, same sheets, sheet-name titles and split tables; a single-row range is a Table here and Text on Reducto; 532 non-empty cells counted against 528 |
| CSV | yes | 100%, one table |
| PPTX | yes, read natively | 100%; a bullet list is one block here, one block per bullet on Reducto |
| TXT | yes | 100%; a form feed starts a new page here, not on Reducto |
| EML | yes | 100%; headers and body, attachments listed by name |
| MSG | yes (`olefile`) | 89% on a public sample message; same subject, sender, date, attachment count |
| RTF, ODT | with LibreOffice, or macOS `textutil` | 100% (through `textutil` in the test) |
| DOC | with LibreOffice, or macOS `textutil` | 100% of the words through `textutil`, but the table arrives as paragraphs (Reducto: 1 table) |
| XLS, PPT, ODS, ODP | with LibreOffice only | not run: LibreOffice is not installed on the build machine. The conversion path is unit-tested with a stand-in converter. Without LibreOffice the 415 error names the format, the program that is missing (or the `READANYDOC_SOFFICE` path that does not exist) and the install command for the server's system (`brew install --cask libreoffice`, `apt-get install -y libreoffice-core libreoffice-writer libreoffice-calc libreoffice-impress`, `winget install TheDocumentFoundation.LibreOffice`) |
