"""Video-only reference editing; coordinates use the displayed image's exact scale."""
from video_direction_overlay import pixel_from_widget
from reference_ui import inverse_quick_pixel


class VideoReferenceDrag:
    def __init__(self,calibration,geometry,eligible,submit,notify):
        self.calibration=calibration;self.geometry=geometry;self.eligible=eligible
        self.submit=submit;self.notify=notify;self.active=False;self.start_cal=None

    def point(self,event):
        cal=self.calibration();geometry=self.geometry()
        if not cal or not geometry:return None
        widget,image=geometry
        pixel=pixel_from_widget(event.x,event.y,widget,image,raw_size=(1280,720))
        return inverse_quick_pixel(cal,*pixel) if pixel else None

    def begin(self,event):
        self.active=False
        allowed,reason=self.eligible()
        if not allowed:self.notify(reason);return
        point=self.point(event)
        if point is None:self.notify('此处没有已验证的声画映射，未发送参考设置');return
        self.start_cal=self.calibration();self.active=True;self.move(event)

    def move(self,event):
        if not self.active:return
        point=self.point(event)
        if point is None:self.cancel();self.notify('已移出有效校准范围，取消参考设置');return
        self.notify(f'参考 B 待提交：{point[0]:+.1f}° / {point[1]:+.1f}°（松开提交）')

    def release(self,event):
        was_active=self.active;self.active=False
        if not was_active:return False
        allowed,reason=self.eligible()
        if not allowed:self.notify(reason);return False
        if self.calibration() is not self.start_cal:self.notify('校准已改变，取消本次参考设置');return False
        point=self.point(event)
        if point is None:self.notify('已移出有效校准范围，未发送参考设置');return False
        return self.submit(*point)

    def cancel(self,event=None):self.active=False;self.start_cal=None
