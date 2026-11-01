import type {ReactNode} from 'react';

export type IconName =
 | 'panel-left' | 'chevron-left' | 'chevron-right' | 'chevron-down'
 | 'plus' | 'search' | 'message' | 'grid' | 'activity' | 'book'
 | 'flask' | 'settings' | 'shield' | 'folder' | 'close' | 'arrow-right'
 | 'upload' | 'file-check' | 'table' | 'edit' | 'trash' | 'check' | 'arrow-up' | 'arrow-left'
 | 'refresh' | 'clock' | 'more' | 'prompt';

const shapes:Record<IconName,ReactNode>={
 'panel-left':<><rect x="3" y="4" width="18" height="16" rx="3"/><path d="M9 4v16"/></>,
 'chevron-left':<path d="m14 6-6 6 6 6"/>,
 'chevron-right':<path d="m10 6 6 6-6 6"/>,
 'chevron-down':<path d="m6 9 6 6 6-6"/>,
 plus:<path d="M12 5v14M5 12h14"/>,
 search:<><circle cx="10.5" cy="10.5" r="6.5"/><path d="m15.5 15.5 4.5 4.5"/></>,
 message:<path d="M21 11.5a8.5 8.5 0 0 1-8.5 8.5H4l-2 2V11.5A8.5 8.5 0 0 1 10.5 3h2a8.5 8.5 0 0 1 8.5 8.5Z"/>,
 grid:<><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></>,
 activity:<path d="M3 12h4l3-8 4 16 3-8h4"/>,
 book:<><path d="M12 5c-3-2-6-2-9-1v15c3-1 6-1 9 1 3-2 6-2 9-1V4c-3-1-6-1-9 1Zm0 0v15"/><path d="M6 8h3m6 0h3M6 12h3m6 0h3"/></>,
 flask:<><path d="M9 3h6M10 3v6l-6 9a2 2 0 0 0 1.7 3h12.6a2 2 0 0 0 1.7-3l-6-9V3M7 14h10"/><path d="M10 17h.01M14 18h.01"/></>,
 settings:<><path d="m10 3-.6 2.1-2 .9-2-.6-2 3.4L5 10.3v2.4l-1.6 1.5 2 3.4 2-.6 2 .9L10 20h4l.6-2.1 2-.9 2 .6 2-3.4-1.6-1.5v-2.4l1.6-1.5-2-3.4-2 .6-2-.9L14 3Z"/><circle cx="12" cy="11.5" r="3"/></>,
 shield:<><path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6Z"/><path d="m8.5 12 2.5 2.5 4.5-5"/></>,
 folder:<path d="M3 7V6a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z"/>,
 close:<path d="m6 6 12 12M6 18 18 6"/>,
 'arrow-right':<path d="M4 12h16m-6-6 6 6-6 6"/>,
 'arrow-left':<path d="M20 12H4m6-6-6 6 6 6"/>,
 'arrow-up':<path d="M12 20V4m-6 6 6-6 6 6"/>,
 upload:<><path d="M12 16V3m-5 5 5-5 5 5M4 15v4a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-4"/></>,
 'file-check':<><path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9Zm0 0v6h6"/><path d="m8 15 2.5 2.5L16 12"/></>,
 table:<><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18M3 14h18M9 9v11M15 9v11"/></>,
 edit:<><path d="m15 5 4 4M4 20l4.5-1L20 7.5a2.8 2.8 0 0 0-4-4L4.5 15Z"/><path d="M13 20h7"/></>,
 trash:<><path d="M3 6h18M9 6V3h6v3M5 6l1 14h12l1-14M10 10v6M14 10v6"/></>,
 check:<path d="m5 12 4.5 4.5L19 7"/>,
 refresh:<><path d="M20 7v5h-5M4 17v-5h5"/><path d="M6 6a8 8 0 0 1 13 2l1 4M4 12l1 4a8 8 0 0 0 13 2"/></>,
 clock:<><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></>,
 more:<><circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/></>,
 prompt:<><rect x="3" y="3" width="18" height="18" rx="3"/><path d="M7 8h10M7 12h7M7 16h5"/></>,
};

export function Icon({name,size=18,className=''}:{name:IconName;size?:number;className?:string}){
 return <svg className={`icon${className?` ${className}`:''}`} width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">{shapes[name]}</svg>;
}
