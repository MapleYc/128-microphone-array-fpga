"""Build independent CoreMark firmware; no device, simulator or FPGA tool is invoked."""
from pathlib import Path
import argparse,base64,csv,hashlib,os,re,shutil,struct,subprocess

ROOT=Path(__file__).resolve().parent
ALGORITHMS=['core_list_join.c','core_main.c','core_matrix.c','core_state.c','core_util.c']
FLAGS=['-march=rv32i_zmmul','-mabi=ilp32','-mstrict-align','-std=c11','-O2','-fno-if-conversion','-fno-if-conversion2','-funroll-all-loops','-ffreestanding','-fno-builtin','-fno-common','-ffunction-sections','-fdata-sections','-fno-pic','-fno-pie','-fno-delete-null-pointer-checks','-msmall-data-limit=8','-Wall','-Wextra','-DPLATFORM_ISA="RV32I_ZMMUL"','-DPLATFORM_LABEL="ARRAY128_CPU"','-DPLATFORM_HZ=63000000u','-DSTANDALONE=1','-DTOTAL_DATA_SIZE=2000']
ALLOWED=set('lui auipc jal jalr beq bne blt bge bltu bgeu lb lh lw lbu lhu sb sh sw addi slti sltiu xori ori andi slli srli srai add sub sll slt sltu xor srl sra or and fence mul mulh mulhsu mulhu'.split())
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()

def verify_sources():
    with (ROOT/'SOURCE_PROVENANCE.csv').open(encoding='utf-8',newline='') as h:
        rows=list(csv.DictReader(h))
    official=[r for r in rows if r['origin'].startswith('EEMBC official')]
    for row in official:
        if sha(ROOT/row['file'])!=row['copied_source_sha256']:raise ValueError('Official source changed: '+row['file'])
    if len(official)!=10:raise ValueError('Incomplete pinned source and license set')
    return len(official)

def image_bytes(elf):
    data=Path(elf).read_bytes()
    if data[:7]!=b'\x7fELF\x01\x01\x01':raise ValueError('Expected ELF32 little endian')
    h=struct.unpack_from('<16sHHIIIIIHHHHHH',data)
    _,kind,machine,version,entry,phoff,shoff,flags,ehsize,phsize,phnum,*_=h
    if kind!=2 or machine!=243 or flags!=0 or entry!=0:raise ValueError('Unexpected ELF target, flags or entry')
    regions=[('imem',0,bytearray(65536)),('dmem',0x10000000,bytearray(65536))];segments=[]
    for n in range(phnum):
        ptype,offset,vaddr,paddr,filesz,memsz,pflags,align=struct.unpack_from('<8I',data,phoff+n*phsize)
        if ptype!=1 or not memsz:continue
        if filesz>memsz or offset+filesz>len(data) or paddr!=vaddr:raise ValueError('Invalid ELF load segment')
        for name,base,blob in regions:
            if base<=vaddr and vaddr+memsz<=base+len(blob):
                start=vaddr-base;blob[start:start+filesz]=data[offset:offset+filesz]
                segments.append((name,vaddr,filesz,memsz,pflags));break
        else:raise ValueError('ELF segment outside 64 KiB IMEM/DMEM')
    if {s[0]for s in segments}!={'imem','dmem'}:raise ValueError('Missing instruction/data segment')
    images={name+'.hex':''.join(f'{int.from_bytes(blob[i:i+4],"little"):08x}\n' for i in range(0,len(blob),4)).encode('ascii') for name,base,blob in regions}
    return images,segments

def write_commands(path,commands):
    with path.open('w',encoding='utf-8',newline='') as h:
        w=csv.writer(h,lineterminator='\n');w.writerow(['step','argument','utf8_base64url'])
        for step,c in enumerate(commands):
            for i,v in enumerate(c):w.writerow([step,i,base64.urlsafe_b64encode(str(v).encode('utf-8')).decode('ascii')])

def execute(command,output,name,commands):
    command=list(map(str,command));commands.append(command)
    result=subprocess.run(command,cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,creationflags=0x08000000 if os.name=='nt' else 0)
    (output/(name+'.log')).write_bytes(result.stdout)
    if result.returncode:raise RuntimeError('Command failed: '+name+'; see '+str(output/(name+'.log')))
    return result.stdout.decode('utf-8',errors='replace')

def compile_mode(tc,mode,iterations):
    output=ROOT/'build'/mode;output.mkdir(parents=True,exist_ok=True);relative=output.relative_to(ROOT).as_posix()
    commands=[];gcc=tc/'riscv-none-elf-gcc.exe'
    flags=[*FLAGS,f'-DITERATIONS={iterations}','-DPERFORMANCE_RUN=1' if mode=='performance' else '-DVALIDATION_RUN=1']
    link=['-nostdlib','-nostartfiles','-static','-Wl,--gc-sections','-Wl,--relax','-Tlink.ld']
    recorded=' '.join(flags+link+['-lgcc'])
    literal=recorded.replace('\\','\\\\').replace('"','\\"')
    (output/'build_flags.h').write_text('#define BUILD_FLAGS "'+literal+'"\n',encoding='ascii',newline='\n')
    include=['-I.','-Iport','-Ivendor/coremark','-I'+relative]
    sources=['start.S','platform.c',*['vendor/coremark/'+s for s in ALGORITHMS],'port/core_portme.c']
    objects=[]
    try:
        for source in sources:
            obj=relative+'/'+Path(source).stem+'.o';objects.append(obj)
            execute([gcc,*flags,*include,'-c',source,'-o',obj],output,'compile_'+Path(source).stem,commands)
        elf=relative+'/program.elf'
        execute([gcc,*flags,*link,'-Wl,-Map,'+relative+'/program.map',*objects,'-lgcc','-o',elf],output,'link',commands)
        disasm=execute([tc/'riscv-none-elf-objdump.exe','-d','-M','no-aliases,numeric',elf],output,'instructions',commands)
        invalid=[];seen=set()
        for line in disasm.splitlines():
            m=re.match(r'\s*[0-9a-f]+:\s+([0-9a-f]+)\s+(\S+)',line)
            if not m:continue
            encoding,mnemonic=m.groups();seen.add(mnemonic)
            if len(encoding)!=8 or mnemonic not in ALLOWED:invalid.append(line)
        if invalid or 'mul' not in seen:raise ValueError('Linked executable ISA audit failed: '+'; '.join(invalid[:8]))
        libgcc=execute([gcc,'-march=rv32i_zmmul','-mabi=ilp32','-print-libgcc-file-name'],output,'libgcc',commands).strip()
        if 'rv32i/ilp32/libgcc.a' not in libgcc.replace('\\','/'):raise ValueError('Wrong libgcc multilib')
        size=execute([tc/'riscv-none-elf-size.exe','-A',elf],output,'sections',commands)
        compiler=execute([gcc,'--version'],output,'compiler',commands).splitlines()[0]
        images,segments=image_bytes(ROOT/elf)
        for name,blob in images.items():(output/name).write_bytes(blob)
        for name,blob in images.items():
            words=(output/name).read_text(encoding='ascii').splitlines()
            recovered=b''.join(int(word,16).to_bytes(4,'little')for word in words)
            if len(words)!=16384 or len(recovered)!=65536:raise ValueError('Image word count mismatch')
            if ''.join(f'{int.from_bytes(recovered[i:i+4],"little"):08x}\n'for i in range(0,65536,4)).encode()!=blob:raise ValueError('Image roundtrip failed')
        report=['status=COMPILED_STATIC_CHECKS_PASS_NOT_EXECUTED','mode='+mode,'iterations='+str(iterations),'timer_hz=63000000','uart_baud=921600','compiler='+compiler,'elf_sha256='+sha(ROOT/elf),'official_algorithm_files_unmodified=true','ISA=rv32i_zmmul/ilp32','unsupported_linked_instructions=0','elf_to_hex_roundtrip=true','CRC_runtime_validation=NOT_EXECUTED','timed_score=NOT_MEASURED','board_execution=NOT_PERFORMED','FPGA_bitstream=NOT_GENERATED','libgcc='+libgcc,'mnemonics='+','.join(sorted(seen))]
        report.extend(f'{name}_segment_address=0x{addr:08x};file_bytes={fs};memory_bytes={ms};flags={fl}' for name,addr,fs,ms,fl in segments)
        report.extend(['','actual_common_flags='+recorded,'','ELF sections:',size])
        (output/'BUILD_REPORT.txt').write_text('\n'.join(report)+'\n',encoding='utf-8',newline='\n')
        print(mode+': compiled; static ISA/ELF/HEX checks passed; runtime CRC and board score not measured.',flush=True)
    finally:write_commands(output/'commands.csv',commands)

def check():
    count=verify_sources()
    sums=ROOT/'SHA256SUMS'
    if not sums.is_file():raise ValueError('Missing source integrity list')
    checked=set()
    for line in sums.read_text(encoding='utf-8').splitlines():
        digest,name=line.split('  ',1)
        target=(ROOT/name).resolve()
        if len(digest)!=64 or any(c not in '0123456789abcdef'for c in digest):raise ValueError('Invalid source digest')
        if name in checked or not target.is_relative_to(ROOT.resolve()):raise ValueError('Invalid or duplicate source path '+name)
        if not target.is_file() or sha(target)!=digest:raise ValueError('Source file mismatch '+name)
        checked.add(name)
    required=['build.py','check_log.py','link.ld','platform.c','platform.h','start.S','port/core_portme.c','port/core_portme.h','SOURCE_PROVENANCE.csv','README.txt','UPSTREAM_SOURCE.txt']+['vendor/coremark/'+name for name in ALGORITHMS+['coremark.h','LICENSE.md','README.md','barebones_porting.md','coremark.md5']]
    if not set(required).issubset(checked):raise ValueError('Source integrity list omits a required dependency')
    include_dirs=[ROOT,ROOT/'port',ROOT/'vendor/coremark']
    for name in required:
        source=ROOT/name
        if source.suffix.lower()not in ['.c','.h','.s']:continue
        for header in re.findall(r'^\s*#\s*include\s*"([^"]+)"',source.read_text(encoding='utf-8'),re.M):
            if header=='build_flags.h':continue
            if not any(p.is_file()for p in [source.parent/header,*[d/header for d in include_dirs]]):raise ValueError('Missing source include '+name+': '+header)
    print('PASS: source-only integrity, '+str(count)+' pinned upstream files, compiler inputs, generated-header contract and local include closure. No ELF, HEX, map, compiler or hardware is required.')

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--check',action='store_true');parser.add_argument('--toolchain');parser.add_argument('--mode',choices=['all','performance','validation'],default='all');parser.add_argument('--iterations',type=int,default=10000)
    args=parser.parse_args()
    if args.check:check();return
    if args.iterations<=0:parser.error('iterations must be positive')
    verify_sources()
    directory=args.toolchain or os.environ.get('RISCV_TOOLCHAIN')
    found=shutil.which('riscv-none-elf-gcc.exe')
    if not directory and found:directory=str(Path(found).parent)
    if not directory:parser.error('Set --toolchain or RISCV_TOOLCHAIN to xPack RISC-V GCC 15.2.0-1 bin directory')
    tc=Path(directory).resolve()
    for mode in ['performance','validation'] if args.mode=='all' else [args.mode]:compile_mode(tc,mode,args.iterations)

if __name__=='__main__':main()
