import {useState} from 'react';
import {Button,Modal,Typography} from 'antd';
import {MailOutlined} from '@ant-design/icons';
import './home-contact.css';

const email='li.zhichao5@zte.com.cn';

export default function HomeContact(){
 const [open,setOpen]=useState(false);
 return <div className="home-contact">
  <Button type="link" icon={<MailOutlined aria-hidden="true"/>} onClick={()=>setOpen(true)}>
   <span>反馈与建议</span><span className="home-contact-address">{email}</span>
  </Button>
  <Modal open={open} title="反馈与建议" onCancel={()=>setOpen(false)} footer={<Button type="primary" onClick={()=>setOpen(false)}>知道了</Button>}>
   <div className="home-contact-details">
    <p>如有意见或建议，欢迎发送邮件至以下邮箱。感谢你的反馈。</p>
    <Typography.Text copyable={{text:email,tooltips:['复制邮箱','已复制']}}><a href={`mailto:${email}`}>{email}</a></Typography.Text>
    <p><a href="https://github.com/Laurence688/Bidding-Agent/issues" target="_blank" rel="noopener noreferrer">也可以在 GitHub 提交反馈与建议</a></p>
   </div>
  </Modal>
 </div>;
}
