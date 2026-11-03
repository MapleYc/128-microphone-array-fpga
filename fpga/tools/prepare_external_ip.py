"""Check IP dependencies or generate them in a separate local workspace.

Generated vendor implementation files are external dependencies, not public
source in this template. Generation requires an authorized local PDS install.
"""
from pathlib import Path
import argparse, csv, hashlib, os, re, shutil, subprocess, sys, time
import xml.etree.ElementTree as ET

DESCRIPTORS = [
    "cpu_memory/rv32_imem/rv32_imem.idf",
    "cpu_memory/rv32_dmem/rv32_dmem.idf",
    "source/vendor/mini200_ddr/DDR_200H/DDR_200H.idf",
]
INIT_PARAMETERS = [
    "cpu_memory/rv32_imem/rtl/rv32_imem_init_param.v",
    "cpu_memory/rv32_dmem/rtl/rv32_dmem_init_param.v",
]

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def inside(root, value):
    result=(root/value).resolve()
    if root.resolve() not in result.parents:
        raise ValueError("Dependency escapes the project: "+str(value))
    return result

def params(tree):
    return {x.findtext("name"):x.findtext("value") for x in tree.getroot().findall("./param_list/param")}

def validate_idf(path):
    tree=ET.parse(path);header=tree.getroot().find("header")
    expected={"family":"Logos2","device":"PG2L200H","package":"FBB676","speedgrade":"-6"}
    if any(header.findtext(k)!=v for k,v in expected.items()):
        raise ValueError("Unexpected IP target: "+str(path))
    name=path.stem;p=params(tree)
    geometry={"ADDR_WIDTH":"14","DATA_WIDTH":"32","INIT_EN":"true"} if name=="rv32_imem" else {"RD_ADDR_WIDTH":"14","WR_ADDR_WIDTH":"14","RD_DATA_WIDTH":"32","WR_DATA_WIDTH":"32","WR_BYTE_EN":"true","INIT_EN":"true"} if name=="rv32_dmem" else {}
    if any(p.get(k)!=v for k,v in geometry.items()):
        raise ValueError("Unexpected memory geometry: "+str(path))
    return tree

def required_files(project):
    files=set()
    text=(project/"ArrayAcquisition.pds").read_text(encoding="utf-8")
    files.update(inside(project,x) for x in re.findall(r'\(_(?:file|ip|ip_source_item) "([^"\r\n]+)"',text))
    include_dirs=[project/"source/vendor/MEMS_Array_wrapper/rtl",project/"raw_transport/include",project/"source/rtl/video"]
    for rel in DESCRIPTORS:
        idf=inside(project,rel);files.add(idf);tree=validate_idf(idf)
        files.update(inside(project,str((idf.parent/row.attrib["pathname"]).relative_to(project))) for row in tree.getroot().findall("./file_list/source/file"))
        p=params(tree)
        if p.get("INIT_EN")=="true":files.add(inside(project,p["INIT_FILE"]))
    files.update(inside(project,x) for x in INIT_PARAMETERS)
    queue=list(files);seen=set()
    while queue:
        path=queue.pop()
        if path in seen or not path.is_file():continue
        seen.add(path)
        if path.suffix.lower()not in{".v",".sv",".vp",".vh",".svh"}:continue
        text=path.read_bytes().decode("utf-8",errors="replace")
        for name in re.findall(r'(?m)^\s*`include\s+"([^"\r\n]+)"',text):
            matches=[folder/name for folder in [path.parent,*include_dirs] if(folder/name).is_file()]
            target=matches[0].resolve()if matches else(path.parent/name).resolve()
            if project.resolve()not in target.parents:raise ValueError("Include escapes project")
            if target not in files:files.add(target);queue.append(target)
    return sorted(files)

def check(project):
    files=required_files(project);missing=[str(p.relative_to(project)).replace("\\","/")for p in files if not p.is_file()]
    for rel in DESCRIPTORS[:2]:
        tree=validate_idf(project/rel);image=inside(project,params(tree)["INIT_FILE"])
        words=image.read_text(encoding="ascii").split()
        if len(words)!=16384 or any(not re.fullmatch(r"[0-9a-fA-F]{8}",w)for w in words):
            raise ValueError("Expected exactly 16384 plain 32-bit HEX words: "+str(image))
    return files,missing

def run_generator(exe,idf,logs,timeout,memory_cap,license_file):
    try:import psutil
    except ImportError:raise RuntimeError("Bounded generation requires psutil; install it before --generate")
    forbidden={"pds_shell.exe","psynth.exe","mpnr.exe","vlog.exe","vopt.exe","vsim.exe","ip_generate.exe"}
    active=[]
    for process in psutil.process_iter(["pid","name"]):
        try:
            if(process.info["name"]or"").lower()in forbidden:active.append(process.info)
        except(psutil.AccessDenied,psutil.NoSuchProcess):pass
    if active:raise RuntimeError("Another EDA/IP worker is active; generator was not started")
    if psutil.virtual_memory().available<3*1024**3:raise RuntimeError("Insufficient available memory")
    env=dict(os.environ)
    if license_file:env["PANGO_LICENSE_FILE"]=str(license_file.resolve())
    flags=(subprocess.CREATE_NO_WINDOW|subprocess.BELOW_NORMAL_PRIORITY_CLASS)if os.name=="nt"else 0
    log=logs/(idf.stem+"_console.log");started=time.monotonic();peak=0;reason=None;known={}
    with log.open("wb")as output:
        proc=subprocess.Popen([str(exe),"-i",str(idf)],cwd=idf.parent,env=env,stdout=output,stderr=subprocess.STDOUT,creationflags=flags)
        try:
            while proc.poll()is None:
                try:tree=[psutil.Process(proc.pid)]+psutil.Process(proc.pid).children(recursive=True)
                except psutil.NoSuchProcess:tree=[]
                rss=0
                for child in tree:
                    try:
                        known[child.pid]=child.create_time();rss+=child.memory_info().rss
                        if child.name().lower()in{"psynth.exe","mpnr.exe","vlog.exe","vopt.exe","vsim.exe"}:reason="Full EDA child is forbidden"
                    except(psutil.NoSuchProcess,psutil.AccessDenied):pass
                peak=max(peak,rss)
                if time.monotonic()-started>timeout:reason="Timeout"
                if rss>memory_cap:reason="Memory cap"
                if reason:raise RuntimeError(reason)
                time.sleep(.2)
            code=proc.wait(timeout=10)
        finally:
            if proc.poll()is None:
                try:owned=[psutil.Process(proc.pid)]+psutil.Process(proc.pid).children(recursive=True)
                except psutil.NoSuchProcess:owned=[]
                for child in reversed(owned):
                    try:child.kill()
                    except(psutil.NoSuchProcess,psutil.AccessDenied):pass
                proc.wait(timeout=10)
            residual=[]
            for pid,created in known.items():
                try:
                    child=psutil.Process(pid)
                    if child.create_time()==created and child.is_running():
                        residual.append(pid);child.kill()
                except(psutil.NoSuchProcess,psutil.AccessDenied):pass
            if residual:raise RuntimeError("Generator left owned child processes; stopped them: "+str(residual))
    native=idf.parent/"generate.log"
    text=native.read_text(encoding="utf-8",errors="replace")if native.is_file()else""
    if code or reason or "Done: 0 error(s)"not in text:
        raise RuntimeError(f"{idf.stem} generation failed (exit {code}, {reason or 'native completion not found'}); inspect local logs")
    return {"IP":idf.stem,"exit":code,"seconds":round(time.monotonic()-started,3),"peak_RSS_bytes":peak}

def generate(source,out,pds_bin,timeout,memory_cap,license_file):
    source=source.resolve();out=out.resolve()
    if out.exists()or source==out or source in out.parents or out in source.parents:
        raise ValueError("Choose a new workspace outside the source project")
    for parent in [source,*source.parents]:
        if(parent/".git").exists()and(parent==out or parent in out.parents):
            raise ValueError("Generated vendor IP must remain outside the Git repository")
    if any(p.is_symlink()for p in source.rglob("*")):raise ValueError("Symlink inputs require manual review")
    exe=(pds_bin/"ip_generate.exe").resolve()
    if not exe.is_file():raise ValueError("ip_generate.exe not found in --pds-bin")
    # Verify configuration/images before creating a new directory. Missing generated IP is expected.
    check(source)
    shutil.copytree(source,out,ignore=shutil.ignore_patterns(".git","__pycache__","build_from_source","compile","synthesize","device_map","place_route","report_timing","generate_bitstream","_external_ip_logs"))
    logs=out/"_external_ip_logs";logs.mkdir();rows=[]
    for rel in DESCRIPTORS:
        idf=out/rel;tree=validate_idf(idf);original=params(tree);image=None
        for item in tree.getroot().findall("./param_list/param"):
            if item.findtext("name")=="INIT_FILE"and original.get("INIT_EN")=="true":
                image=inside(out,original["INIT_FILE"]);item.find("value").text=image.as_posix()
        tree.write(idf,encoding="utf-8",xml_declaration=True)
        row=run_generator(exe,idf,logs,timeout,memory_cap,license_file)
        generated=validate_idf(idf);new=params(generated)
        if any(v!=new.get(k)for k,v in original.items()if k!="INIT_FILE"):
            raise ValueError("Generator changed IP parameters; workspace needs manual review")
        # Restore portable configuration paths after official INIT constants are generated.
        if image:
            for item in generated.getroot().findall("./param_list/param"):
                if item.findtext("name")=="INIT_FILE":item.find("value").text=original["INIT_FILE"]
            for path in idf.parent.rglob("*.v"):
                data=path.read_bytes();before=image.as_posix().encode();after=original["INIT_FILE"].encode()
                if before in data:path.write_bytes(data.replace(before,after))
        generated.write(idf,encoding="utf-8",xml_declaration=True);rows.append(row)
    files,missing=check(out)
    if missing:raise RuntimeError("Generated workspace still lacks dependencies: "+str(missing))
    with(logs/"generation_summary.csv").open("w",encoding="utf-8",newline="")as f:
        writer=csv.DictWriter(f,fieldnames=["IP","exit","seconds","peak_RSS_bytes"]);writer.writeheader();writer.writerows(rows)
    print("External IP generated in the separate workspace. This is not synthesis, timing, board validation, or public redistribution permission.")
    print("Required files checked:",len(files))

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project",type=Path,required=True,help="Project root containing ArrayAcquisition.pds")
    parser.add_argument("--check",action="store_true")
    parser.add_argument("--generate",action="store_true")
    parser.add_argument("--out",type=Path,help="New workspace outside source/repository")
    parser.add_argument("--pds-bin",type=Path)
    parser.add_argument("--license",type=Path,help="Optional local license; passed to this process only")
    parser.add_argument("--timeout",type=int,default=120)
    parser.add_argument("--max-rss-mib",type=int,default=1536)
    args=parser.parse_args();project=args.project.resolve()
    try:
        if args.generate:
            if not args.out or not args.pds_bin:parser.error("--generate requires --out and --pds-bin")
            generate(project,args.out,args.pds_bin,args.timeout,args.max_rss_mib*1024**2,args.license);return 0
        files,missing=check(project)
        print("Required input files:",len(files))
        if missing:
            print("External generated IP dependencies are absent. No stub or synthesis was started.")
            for path in missing:print("MISSING",path)
            return 2
        print("Dependency closure is present. This is a static file check, not EDA validation.");return 0
    except(Exception)as error:
        print("ERROR:",error,file=sys.stderr);return 1

if __name__=="__main__":raise SystemExit(main())
