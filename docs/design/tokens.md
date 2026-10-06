# Studio design specification

Reference: studio-concept.png, generated with built-in image generation for the complete primary studio screen.

White surfaces #ffffff; cool workspace #eef2f5; text #17202a; muted #657184; divider #e0e7eb; teal #127568; selected #e7f3f2. Sidebar 220px, device rail 196px, inspector 290px, header 104px. Space scale 4/8/12/16/24/32; border radius 4px for controls, 6px for workspace; controls 14px/20px, headings 28px/36px semibold; body 14px/22px. System Chinese UI font. Outline Lucide icons at 18px, stroke 1.75. Pure white editor controls, cool grey canvas, monochrome pixel-exact image inside a 5px dark frame. Small semantic statuses only when needed for real task state.

Families: shell navigation, device rows, editor toolbar, pixel canvas, widget inspector, form controls, activity table, dialogs, status table. Browser controls remain native React; generated concept is never used as application UI.

Allowed primary copy: epapers, 墨水屏工作台, 管理每一块屏幕的内容与状态, 添加屏幕, 页面编辑, 设备状态, 我的屏幕, 页面模板, 添加文本, 添加实体, 添加时钟, 撤销, 属性, 名称, 屏幕型号, 字体, 温度实体, 湿度实体, 保存页面, 更新屏幕, 最近活动.

Intentional extensions required by accepted product spec: live drag selection and widget property controls, template gallery, gateway/log/settings views, import/export, real HA entity autocomplete, empty states, notices for unqualified hardware, viewport adaptation. Demo devices appear only in explicit demo mode, named and labelled simulated. Concept example timestamp is replaced with actual timestamps. No imaginary battery, connectivity, firmware or successful refresh data.
