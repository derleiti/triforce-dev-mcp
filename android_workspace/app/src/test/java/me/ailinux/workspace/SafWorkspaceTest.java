package me.ailinux.workspace;
import org.junit.Test;
import static org.junit.Assert.*;
public class SafWorkspaceTest {
 @Test public void cleanPathRejectsTraversal(){try{SafWorkspace.cleanPath("a/../b");fail();}catch(IllegalArgumentException expected){}}
 @Test public void cleanPathNormalizesRelative(){assertEquals("a/b",SafWorkspace.cleanPath("./a/b"));}
 @Test public void cleanPathRejectsAbsolute(){try{SafWorkspace.cleanPath("/etc/passwd");fail();}catch(IllegalArgumentException expected){}}
}
