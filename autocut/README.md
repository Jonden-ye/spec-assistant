# 剪间

自动剪视频工作台。上传一条片子之后，它会量音量、找停顿、标镜头切换，在时间轴上给出一版可改的剪辑，再导出 H.264。

处理都在本机，用的是 ffmpeg，片子不会发到别的服务。

## 运行

需要本机有 `ffmpeg` 和 `ffprobe`。

```bash
cd autocut
pip install -r requirements.txt
python server.py
```

浏览器打开 http://127.0.0.1:8765 。没有现成素材时，点「用示例口播试剪」。

## 三种方案

- **去气口**：低于阈值、并且够长的停顿会剪掉，句与句之间可以留一点气口。
- **高光**：按音量从高到低取不重叠的片段，直到凑够目标时长。
- **镜头**：按画面变化切开。可以再去掉基本没声音的镜头。

时间轴上可以改保留或去掉、拖边界、在播放头切开。成片预览会跳过剪掉的部分。导出可选原比例、16:9、9:16、1:1，竖屏和方形按画面中心裁切。

## 测试

```bash
node --test plan.test.js
python -m unittest test_edit.py
python server.py   # 另开一个终端
python test_api.py
```
