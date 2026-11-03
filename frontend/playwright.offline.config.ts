import {defineConfig} from '@playwright/test';
export default defineConfig({testDir:'../tests/browser',testMatch:'offline-network-tools.spec.ts',workers:1,timeout:30000,expect:{timeout:10000},outputDir:'../.cache/offline-tools-browser',reporter:[['list']],use:{channel:process.env.LAURA_BROWSER_CHANNEL??'chrome',headless:true,acceptDownloads:true}});
