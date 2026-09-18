from pathlib import Path
import ast,re,sys
p=Path(sys.argv[1])
s=p.read_text()
old="TEAMS=dict(zip('ARI ATL BAL BUF CAR CHI CIN CLE DAL DEN DET GB HOU IND JAX KC LA LAC LV MIA MIN NE NO NYG NYJ PHI PIT SEA SF TB TEN WAS'.split(),['Cardinals','Falcons','Ravens','Bills','Panthers','Bears','Bengals','Browns','Cowboys','Broncos','Lions','Packers','Texans','Colts','Jaguars','Chiefs','Rams','Chargers','Raiders','Dolphins','Vikings','Patriots','Saints','Giants','Jets','Eagles','Steelers','49ers','Buccaneers','Titans','Commanders']))"
new="TEAMS=dict(zip('ARI ATL BAL BUF CAR CHI CIN CLE DAL DEN DET GB HOU IND JAX KC LA LAC LV MIA MIN NE NO NYG NYJ PHI PIT SEA SF TB TEN WAS'.split(),['Cardinals','Falcons','Ravens','Bills','Panthers','Bears','Bengals','Browns','Cowboys','Broncos','Lions','Packers','Texans','Colts','Jaguars','Chiefs','Rams','Chargers','Raiders','Dolphins','Vikings','Patriots','Saints','Giants','Jets','Eagles','Steelers','Seahawks','49ers','Buccaneers','Titans','Commanders']))"
if old not in s:
    if new in s:
        print('already fixed')
    else:
        raise SystemExit('team-map target not found')
else:
    s=s.replace(old,new,1)
# Add fail-closed integrity assertion immediately after mapping.
anchor=new
assertion="\nassert len(TEAMS)==32 and TEAMS['SEA']=='Seahawks' and TEAMS['SF']=='49ers' and TEAMS['TB']=='Buccaneers' and TEAMS['TEN']=='Titans' and TEAMS['WAS']=='Commanders', 'NFL team-name mapping corrupted'\n"
if "NFL team-name mapping corrupted" not in s:
    s=s.replace(anchor,anchor+assertion,1)
p.write_text(s)
print('NFL team names fixed and guarded')
