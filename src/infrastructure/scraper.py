"""Playwright-based web scraper implementation."""

import re

from playwright.async_api import async_playwright

from src.domain.entities import PADData, PajakItem, TotalPAD
from src.domain.ports import ScraperPort


class ScrapingError(Exception):
    """Raised when web scraping fails."""


class PlaywrightScraper(ScraperPort):
    """Scrapes PAD data from etax-klaten dashboard using Playwright."""

    # JavaScript to extract table data directly from the DOM
    _EXTRACT_TABLE_JS = """
    () => {
        const tables = document.querySelectorAll('table');
        if (!tables.length) return null;

        // Find the table that contains PAD data
        let targetTable = null;
        for (const table of tables) {
            const text = table.innerText || '';
            if (text.includes('Target') && text.includes('Realisasi')) {
                targetTable = table;
                break;
            }
        }
        if (!targetTable) targetTable = tables[0];

        const rows = Array.from(targetTable.querySelectorAll('tbody tr'));
        const result = [];

        for (const row of rows) {
            const cells = Array.from(row.querySelectorAll('td'));
            if (cells.length >= 4) {
                result.push({
                    cells: cells.map(c => c.innerText.trim())
                });
            }
        }

        // Try to get the total/footer row
        const footerRows = Array.from(targetTable.querySelectorAll('tfoot tr'));
        let totalCells = [];
        if (footerRows.length) {
            totalCells = Array.from(
                footerRows[0].querySelectorAll('td, th')
            ).map(c => c.innerText.trim());
        }

        return { rows: result, totalCells };
    }
    """

    async def scrape_pad_data(self, url: str, tahun: int) -> PADData:
        """Scrape PAD data using headless Chromium.

        Opens the dashboard page, waits for the table to render,
        and extracts all rows via JavaScript evaluation.
        """
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
            )
            try:
                page = await browser.new_page(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    viewport={"width": 1280, "height": 720}
                )
                await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                try:
                    await page.wait_for_selector("table", timeout=60000)
                except Exception as e:
                    page_title = await page.title()
                    text_content = await page.evaluate("document.body.innerText")
                    preview = text_content[:200].replace('\n', ' ') if text_content else ''
                    raise ScrapingError(
                        f"Timeout menunggu tabel. Judul: '{page_title}'. "
                        f"Isi: '{preview}'. Kemungkinan IP Railway diblokir. Detail: {str(e)}"
                    )

                # Allow extra time for dynamic content to fully render
                await page.wait_for_timeout(5000)

                raw_data = await page.evaluate(self._EXTRACT_TABLE_JS)

                if not raw_data or not raw_data.get("rows"):
                    raise ScrapingError(
                        "Tidak dapat menemukan tabel data di halaman."
                    )

                pajak_items = self._parse_rows(raw_data["rows"])
                total = self._parse_total(raw_data.get("totalCells", []), pajak_items)

                return PADData(
                    tahun=tahun,
                    sumber=url,
                    data_target_realisasi_pad=pajak_items,
                    total=total,
                )
            finally:
                await browser.close()

    def _parse_currency(self, text: str) -> int:
        """Convert formatted currency string to integer.

        Examples:
            '1.625.000.000' -> 1625000000
            'Rp 1.625.000.000,00' -> 1625000000
        """
        cleaned = re.sub(r"[^\d]", "", text.split(",")[0])
        return int(cleaned) if cleaned else 0

    def _parse_rows(self, rows: list[dict]) -> list[PajakItem]:
        """Parse raw table rows into PajakItem entities."""
        items = []
        for row in rows:
            cells = row.get("cells", [])
            if len(cells) < 4:
                continue

            # Skip rows that look like headers or totals
            # Cell may contain "1." or "1", strip trailing period
            first_cell = cells[0].strip().rstrip(".")
            if not first_cell.isdigit():
                continue

            no = int(first_cell)
            jenis_pajak = cells[1].strip()
            target_rp = self._parse_currency(cells[2])
            realisasi_rp = self._parse_currency(cells[3])

            # Percentage may be in 5th column or calculated
            persentase = cells[4].strip() if len(cells) > 4 else ""
            if not persentase and target_rp > 0:
                pct = (realisasi_rp / target_rp) * 100
                persentase = f"{pct:.2f}%"

            items.append(
                PajakItem(
                    no=no,
                    jenis_pajak=jenis_pajak,
                    target_rp=target_rp,
                    realisasi_rp=realisasi_rp,
                    persentase=persentase,
                )
            )
        return items

    def _parse_total(
        self, total_cells: list[str], items: list[PajakItem]
    ) -> TotalPAD:
        """Parse total row or compute from items if not available."""
        if len(total_cells) >= 4:
            # Try to find numeric cells for target & realisasi
            numeric_cells = [
                c for c in total_cells if re.search(r"\d", c)
            ]
            if len(numeric_cells) >= 2:
                target = self._parse_currency(numeric_cells[0])
                realisasi = self._parse_currency(numeric_cells[1])
                persentase = numeric_cells[2] if len(numeric_cells) > 2 else ""
                if not persentase and target > 0:
                    pct = (realisasi / target) * 100
                    persentase = f"{pct:.2f}%"
                return TotalPAD(
                    target_rp=target,
                    realisasi_rp=realisasi,
                    persentase=persentase,
                )

        # Fallback: compute totals from individual items
        total_target = sum(item.target_rp for item in items)
        total_realisasi = sum(item.realisasi_rp for item in items)
        pct = (total_realisasi / total_target * 100) if total_target > 0 else 0
        return TotalPAD(
            target_rp=total_target,
            realisasi_rp=total_realisasi,
            persentase=f"{pct:.2f}%",
        )
