import sys,json,math
from pathlib import Path
from PIL import Image,ImageDraw,ImageFont
ROOT=Path(__file__).resolve().parents[1]/'Локальная_разметка';OUT=Path(__file__).resolve().parent
items=json.loads((ROOT/'dataset.json').read_text())['items'];a=json.loads((ROOT/'saved/annotations.json').read_text())['annotations']
font=ImageFont.truetype('/System/Library/Fonts/Supplemental/Arial.ttf',16)
small=ImageFont.truetype('/System/Library/Fonts/Supplemental/Arial.ttf',13)
def panel(item,regions,w=350,h=260):
 im=Image.open(ROOT/item['image']).convert('RGB');im.thumbnail((w,h));out=Image.new('RGB',(w,h),'#eceee8');ox=(w-im.width)//2;oy=(h-im.height)//2;out.paste(im,(ox,oy));d=ImageDraw.Draw(out)
 for n,r in enumerate(regions,1):
  v=r['data'];box=(ox+v['p1']['x']*im.width,oy+v['p1']['y']*im.height,ox+v['p2']['x']*im.width,oy+v['p2']['y']*im.height);d.rectangle(box,outline='#19ff63',width=2);d.text((box[0]+2,box[1]+2),str(n),font=small,fill='black',stroke_width=2,stroke_fill='white')
 return out
for page in range(math.ceil(len(items)/9)):
 sheet=Image.new('RGB',(2160,1010),'white');d=ImageDraw.Draw(sheet)
 for j,item in enumerate(items[page*9:page*9+9]):
  ann=a[item['id']];x=(j%3)*720;y=(j//3)*335
  sheet.paste(panel(item,ann['gold_regions']),(x,y+25))
  if 'review' in item['section']:sheet.paste(panel(item,ann['review_regions']),(x+355,y+25))
  d.text((x+5,y+3),f"{item['section']} #{item['slot']} {item['id']}",font=font,fill='black')
  d.text((x+5,y+290),f"GOLD {ann['gold_status']} [{len(ann['gold_regions'])}]  SHOWN [{len(ann['review_regions'])}] {ann['review_verdict']} {','.join(ann['review_reasons'])}",font=small,fill='black')
  d.text((x+5,y+313),item['scenario'][:65],font=small,fill='#555555')
 sheet.save(OUT/f'page_{page+1:02}.jpg',quality=90)
print('Rendered 8 sheets; gold left, presented version right.')
