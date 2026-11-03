"""Validate a newly captured 63 MHz CoreMark UART log; no device is opened."""
from pathlib import Path
from decimal import Decimal
import argparse,re

def inspect(text,mode,iterations,crc_only=False):
    expected={'seedcrc':0xe9f5,'crclist':0xe714,'crcmatrix':0x1fd7,'crcstate':0x8e3a}
    if mode=='validation':expected={'seedcrc':0x18f2,'crclist':0xe3c1,'crcmatrix':0x0747,'crcstate':0x8d84}
    errors=[]
    for key,value in expected.items():
        values=re.findall(r'\b'+key+r'\s*:\s*0x([0-9a-fA-F]+)',text)
        if len(values)!=1 or int(values[0],16)!=value:errors.append('missing, duplicate or wrong '+key)
    if re.findall(r'Iterations\s*:\s*(\d+)',text)!=[str(iterations)]:errors.append('iteration count mismatch')
    if re.findall(r'CoreMark Size\s*:\s*(\d+)',text)!=['666']:errors.append('CoreMark per-algorithm size mismatch')
    if len(re.findall(r'\bcrcfinal\s*:\s*0x[0-9a-fA-F]+',text))!=1:errors.append('missing or duplicate final CRC')
    if text.count('SIM_CRC_PASS')!=1 or 'SIM_CRC_FAIL' in text:errors.append('missing, duplicate or failed platform CRC status')
    if re.search(r'ERROR! (?:list|matrix|state) crc',text):errors.append('official algorithm CRC error')
    ticks=re.findall(r'SIM_CYCLES_HEX=([0-9a-fA-F]{16}) TIMER_HZ=(\d+)',text)
    cycles,hz=(int(ticks[0][0],16),int(ticks[0][1])) if len(ticks)==1 else (0,0)
    if cycles<=0 or hz!=63000000:errors.append('missing or wrong 63 MHz hardware tick record')
    total=re.findall(r'Total ticks\s*:\s*(\d+)',text)
    if total!=[str(cycles & 0xffffffff)]:errors.append('official low-32 tick field and platform ticks differ')
    banner=re.findall(r'timer_hz=(\d+); iterations=(\d+)',text)
    if banner!=[('63000000',str(iterations))]:errors.append('banner configuration mismatch')
    functional=not errors
    timing_errors=[]
    if not hz or cycles<10*hz:timing_errors.append('measured interval is under 10 seconds')
    if 'SIM_SHORT_RUN' in text:timing_errors.append('short-run marker present')
    if 'ERROR!' in text or 'Errors detected' in text:timing_errors.append('official validation error present')
    if 'Correct operation validated.' not in text:timing_errors.append('official successful completion missing')
    timed=functional and not timing_errors
    result={'functional_crc_checks_pass':str(functional).lower(),'timed_run_checks_pass':str(timed).lower(),'mode':mode,'iterations':str(iterations),'reported_ticks':str(cycles),'timer_hz':str(hz),'scope':'Checks only the supplied log; does not establish its capture provenance or certify a board result.'}
    result['official_total_ticks_is_low32']=str(cycles>0xffffffff).lower()
    if hz and cycles:result['elapsed_seconds']=str(Decimal(cycles)/Decimal(hz))
    if timed:
        result['CoreMark_per_second']=str(Decimal(iterations)*Decimal(hz)/Decimal(cycles))
        result['CoreMark_per_MHz']=str(Decimal(iterations)*Decimal(1000000)/Decimal(cycles))
    result['errors']='; '.join(errors)
    result['timing_errors']='; '.join(timing_errors)
    return functional if crc_only else timed,result

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('log',type=Path);parser.add_argument('--mode',choices=['performance','validation'],required=True);parser.add_argument('--iterations',type=int,default=10000);parser.add_argument('--crc-only',action='store_true',help='Check functional CRC fields only; short runs still produce no score')
    args=parser.parse_args();ok,result=inspect(args.log.read_text(encoding='utf-8',errors='replace'),args.mode,args.iterations,args.crc_only)
    for key,value in result.items():print(key+'='+value)
    raise SystemExit(0 if ok else 1)

if __name__=='__main__':main()
