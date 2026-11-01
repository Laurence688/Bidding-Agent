import {ConfigProvider,App as AntApp} from 'antd';
import zhCN from 'antd/locale/zh_CN';
import type {ReactNode} from 'react';
import './upgrade.css';
export default function LauraTheme({children}:{children:ReactNode}){
 return <ConfigProvider locale={zhCN} theme={{token:{colorPrimary:'#245fe8',colorInfo:'#245fe8',colorSuccess:'#268567',colorWarning:'#b7791f',colorError:'#c34551',colorText:'#20242b',colorTextSecondary:'#6b7280',colorBgLayout:'#f7f8fa',colorBorder:'#e5e7eb',borderRadius:8,fontFamily:'"Segoe UI","Microsoft YaHei",system-ui,sans-serif',fontSize:14,controlHeight:36},components:{Table:{headerBg:'#f4f7fc'},Drawer:{paddingLG:24}}}}><AntApp>{children}</AntApp></ConfigProvider>;
}
