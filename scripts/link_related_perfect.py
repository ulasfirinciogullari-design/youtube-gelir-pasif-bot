import sys, asyncio
sys.stdout.reconfigure(encoding='utf-8')
from pathlib import Path
from playwright.async_api import async_playwright

async def link_related_perfect(short_id: str, target_query: str):
    session_dir = Path(r'output/studio_browser_session').resolve()
    user_agent = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36'
    edit_url = f'https://studio.youtube.com/video/{short_id}/edit'
    
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(session_dir),
            headless=True,
            user_agent=user_agent,
            viewport={'width': 1920, 'height': 1080},
            args=['--disable-blink-features=AutomationControlled']
        )
        page = await context.new_page()
        print(f'Navigating to {edit_url}...')
        await page.goto(edit_url, wait_until='networkidle', timeout=60000)
        await page.wait_for_timeout(3000)
        
        # Click the trigger element
        trigger = page.locator('ytcp-text-dropdown-trigger:has-text("İlgili video"), ytcp-dropdown-trigger:has-text("İlgili video")').first
        print('Clicking related video dropdown trigger...')
        await trigger.click()
        await page.wait_for_timeout(2500)
        
        await page.screenshot(path='output/related_dialog_opened.png')
        print('Saved output/related_dialog_opened.png')
        
        # Now find the search input or result rows inside dialog
        dialog = page.locator('ytcp-video-pick-dialog, [role="dialog"]').first
        if await dialog.is_visible():
            print('Dialog is visible! Searching video...')
            search_input = dialog.locator('input').first
            if await search_input.is_visible():
                await search_input.fill(target_query)
                await page.wait_for_timeout(2000)
                
            # Select the first video row
            first_row = dialog.locator('ytcp-video-row, ytcp-entity-card, [role="row"]').first
            print('Selecting first video result...')
            await first_row.click()
            await page.wait_for_timeout(2000)
            
            # Click Save button (Kaydet)
            save_btn = page.locator('#save-button, button:has-text("Kaydet")').first
            if await save_btn.is_enabled():
                print('Clicking Kaydet button...')
                await save_btn.click()
                await page.wait_for_timeout(3000)
                print('SUCCESS: Related video successfully linked and saved!')
            else:
                print('Save button not enabled yet.')

        await page.screenshot(path='output/related_linked_success.png', full_page=True)
        print('Saved output/related_linked_success.png')
        await context.close()

if __name__ == '__main__':
    asyncio.run(link_related_perfect('-B3cdS07Z9c', 'HOLLYWOOD TRILOGY'))
